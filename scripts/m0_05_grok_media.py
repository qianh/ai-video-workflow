#!/usr/bin/env python3
"""M0-05: probe Grok text + media capabilities via headless CLI.

Each capability is independently classified as ready | degraded | unavailable.
Successful media must land under the task output directory and pass decode checks.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts" / "m0-05"


@dataclass
class CapabilityResult:
    capability: str
    status: str  # ready | degraded | unavailable
    elapsed_sec: float
    detail: str
    outputs: list[str]
    error: str | None = None


def which(name: str) -> str | None:
    return shutil.which(name)


def run_grok(
    prompt: str,
    *,
    out_stdout: Path,
    out_stderr: Path,
    tools: str | None,
    json_schema: dict[str, Any] | None,
    max_turns: int,
    timeout_sec: int,
    yolo: bool = True,
) -> dict[str, Any]:
    grok = which("grok")
    if not grok:
        raise FileNotFoundError("grok CLI not found on PATH")

    command = [
        grok,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--cwd",
        str(ROOT),
    ]
    if yolo:
        command.append("--yolo")
    if tools:
        command.extend(["--tools", tools])
    if json_schema is not None:
        command.extend(["--json-schema", json.dumps(json_schema, separators=(",", ":"))])

    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=os.environ.copy(),
    )
    elapsed = time.perf_counter() - started
    out_stdout.write_text(completed.stdout, encoding="utf-8")
    out_stderr.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"grok exit {completed.returncode}: {completed.stderr[-500:] or completed.stdout[-500:]}"
        )
    payload = json.loads(completed.stdout)
    payload["_elapsed_sec"] = elapsed
    return payload


def copy_if_exists(src: str | Path, dest: Path) -> Path | None:
    path = Path(src)
    if not path.is_file():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return dest


def verify_image(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            return f"PIL {image.format} {image.size[0]}x{image.size[1]} {image.mode}"
    except Exception as exc:  # pragma: no cover - fallback path
        data = path.read_bytes()[:3]
        if data == b"\xff\xd8\xff":
            return f"JPEG magic ok ({path.stat().st_size} bytes); PIL unavailable: {exc}"
        if data == b"\x89PN":
            return f"PNG magic ok ({path.stat().st_size} bytes); PIL unavailable: {exc}"
        raise


def verify_video(path: Path) -> str:
    ffprobe = which("ffprobe")
    if not ffprobe:
        size = path.stat().st_size
        if size <= 0:
            raise RuntimeError("empty video file")
        return f"file exists ({size} bytes); ffprobe missing"
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffprobe failed")
    info = json.loads(completed.stdout)
    streams = info.get("streams") or []
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise RuntimeError("no video stream")
    return json.dumps(info, ensure_ascii=False)


def probe_text(out_dir: Path, timeout_sec: int) -> CapabilityResult:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "capability", "note"],
        "properties": {
            "status": {"type": "string"},
            "capability": {"type": "string"},
            "note": {"type": "string"},
        },
    }
    try:
        payload = run_grok(
            'Return only JSON: {"status":"ok","capability":"text.structured_generate","note":"m0-05 probe"}',
            out_stdout=out_dir / "text.stdout",
            out_stderr=out_dir / "text.stderr",
            tools=None,
            json_schema=schema,
            max_turns=2,
            timeout_sec=timeout_sec,
        )
        structured = payload.get("structuredOutput") or {}
        if structured.get("status") != "ok":
            raise RuntimeError(f"unexpected structured output: {structured}")
        return CapabilityResult(
            capability="text.structured_generate",
            status="ready",
            elapsed_sec=float(payload["_elapsed_sec"]),
            detail="grok -p --json-schema structuredOutput",
            outputs=[str(out_dir / "text.stdout")],
        )
    except Exception as exc:
        return CapabilityResult(
            capability="text.structured_generate",
            status="unavailable",
            elapsed_sec=0.0,
            detail="headless structured text probe failed",
            outputs=[],
            error=str(exc),
        )


def probe_image_generate(out_dir: Path, timeout_sec: int) -> CapabilityResult:
    images = out_dir / "images"
    images.mkdir(parents=True, exist_ok=True)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["image_path", "ok"],
        "properties": {
            "image_path": {"type": "string"},
            "ok": {"type": "boolean"},
        },
    }
    prompt = (
        "Use the image_gen tool to generate one simple 1:1 square illustration for a manhua "
        "night market scene with a glowing USB. After generation, report the absolute path of "
        'the saved image as JSON {"image_path":"...","ok":true}. Do not invent a path.'
    )
    try:
        payload = run_grok(
            prompt,
            out_stdout=out_dir / "image.stdout",
            out_stderr=out_dir / "image.stderr",
            tools="image_gen",
            json_schema=schema,
            max_turns=6,
            timeout_sec=timeout_sec,
        )
        structured = payload.get("structuredOutput") or {}
        source = structured.get("image_path")
        if not structured.get("ok") or not source:
            raise RuntimeError(f"image_gen did not return path: {structured}")
        dest = copy_if_exists(source, images / "generate.jpg")
        if dest is None:
            raise RuntimeError(f"image path missing on disk: {source}")
        detail = verify_image(dest)
        return CapabilityResult(
            capability="image.generate",
            status="ready",
            elapsed_sec=float(payload["_elapsed_sec"]),
            detail=detail,
            outputs=[str(dest), str(out_dir / "image.stdout")],
        )
    except Exception as exc:
        return CapabilityResult(
            capability="image.generate",
            status="unavailable",
            elapsed_sec=0.0,
            detail="image_gen probe failed",
            outputs=[],
            error=str(exc),
        )


def probe_image_edit(out_dir: Path, timeout_sec: int, source_image: Path) -> CapabilityResult:
    images = out_dir / "images"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["image_path", "ok"],
        "properties": {
            "image_path": {"type": "string"},
            "ok": {"type": "boolean"},
        },
    }
    if not source_image.is_file():
        return CapabilityResult(
            capability="image.edit",
            status="unavailable",
            elapsed_sec=0.0,
            detail="missing source image from image.generate",
            outputs=[],
            error=f"source missing: {source_image}",
        )
    prompt = (
        f"Use image_edit on the local file {source_image}. Make the glowing USB cyan and slightly "
        'brighter. Report the absolute path of the edited image as JSON {"image_path":"...","ok":true}. '
        "Only use the real tool output path."
    )
    try:
        payload = run_grok(
            prompt,
            out_stdout=out_dir / "edit.stdout",
            out_stderr=out_dir / "edit.stderr",
            tools="image_edit",
            json_schema=schema,
            max_turns=6,
            timeout_sec=timeout_sec,
        )
        structured = payload.get("structuredOutput") or {}
        source = structured.get("image_path")
        if not structured.get("ok") or not source:
            raise RuntimeError(f"image_edit did not return path: {structured}")
        dest = copy_if_exists(source, images / "edit.jpg")
        if dest is None:
            raise RuntimeError(f"edited image missing on disk: {source}")
        detail = verify_image(dest)
        return CapabilityResult(
            capability="image.edit",
            status="ready",
            elapsed_sec=float(payload["_elapsed_sec"]),
            detail=detail,
            outputs=[str(dest), str(out_dir / "edit.stdout")],
        )
    except Exception as exc:
        return CapabilityResult(
            capability="image.edit",
            status="unavailable",
            elapsed_sec=0.0,
            detail="image_edit probe failed",
            outputs=[],
            error=str(exc),
        )


def probe_image_to_video(out_dir: Path, timeout_sec: int, source_image: Path) -> CapabilityResult:
    videos = out_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    schema = {
        "type": "object",
        "additionalProperties": True,
        "required": ["ok"],
        "properties": {
            "video_path": {"type": ["string", "null"]},
            "ok": {"type": "boolean"},
            "error": {"type": "string"},
        },
    }
    if not source_image.is_file():
        return CapabilityResult(
            capability="video.image_to_video",
            status="unavailable",
            elapsed_sec=0.0,
            detail="missing source image from image.generate",
            outputs=[],
            error=f"source missing: {source_image}",
        )
    prompt = (
        f"Use image_to_video on the local file {source_image}. Animate gentle rain and neon flicker, "
        "duration 6 seconds if supported. Report absolute path of the resulting video as JSON "
        '{"video_path":"...","ok":true} or {"video_path":null,"ok":false,"error":"..."}. '
        "Only use a real tool output path on success."
    )
    try:
        payload = run_grok(
            prompt,
            out_stdout=out_dir / "video.stdout",
            out_stderr=out_dir / "video.stderr",
            tools="image_to_video",
            json_schema=schema,
            max_turns=8,
            timeout_sec=timeout_sec,
        )
        structured = payload.get("structuredOutput") or {}
        text = payload.get("text") or ""
        source = structured.get("video_path")
        if structured.get("ok") and source:
            dest = copy_if_exists(source, videos / "i2v.mp4")
            if dest is None:
                raise RuntimeError(f"video path missing on disk: {source}")
            detail = verify_video(dest)
            return CapabilityResult(
                capability="video.image_to_video",
                status="ready",
                elapsed_sec=float(payload["_elapsed_sec"]),
                detail=detail,
                outputs=[str(dest), str(out_dir / "video.stdout")],
            )

        blob = json.dumps(structured, ensure_ascii=False) + "\n" + text
        if "Zero Data Retention" in blob or "upload_url" in blob:
            return CapabilityResult(
                capability="video.image_to_video",
                status="unavailable",
                elapsed_sec=float(payload["_elapsed_sec"]),
                detail="API rejects video without output.upload_url under ZDR policy",
                outputs=[str(out_dir / "video.stdout")],
                error=structured.get("error") or "ZDR requires output.upload_url",
            )
        return CapabilityResult(
            capability="video.image_to_video",
            status="unavailable",
            elapsed_sec=float(payload["_elapsed_sec"]),
            detail="image_to_video returned no usable video path",
            outputs=[str(out_dir / "video.stdout")],
            error=str(structured),
        )
    except Exception as exc:
        return CapabilityResult(
            capability="video.image_to_video",
            status="unavailable",
            elapsed_sec=0.0,
            detail="image_to_video probe failed",
            outputs=[],
            error=str(exc),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--timeout-sec", type=int, default=300)
    parser.add_argument(
        "--skip-video",
        action="store_true",
        help="Skip image_to_video probe (still records unavailable if not run).",
    )
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[CapabilityResult] = []
    results.append(probe_text(out_dir, args.timeout_sec))

    image = probe_image_generate(out_dir, args.timeout_sec)
    results.append(image)
    source_image = out_dir / "images" / "generate.jpg"
    results.append(probe_image_edit(out_dir, args.timeout_sec, source_image))

    if args.skip_video:
        results.append(
            CapabilityResult(
                capability="video.image_to_video",
                status="unavailable",
                elapsed_sec=0.0,
                detail="skipped by --skip-video",
                outputs=[],
                error="skipped",
            )
        )
    else:
        results.append(probe_image_to_video(out_dir, args.timeout_sec, source_image))

    # Gate: image.generate is hard requirement for V1 media path.
    image_ready = any(
        item.capability == "image.generate" and item.status == "ready" for item in results
    )
    text_ready = any(
        item.capability == "text.structured_generate" and item.status == "ready"
        for item in results
    )
    accepted = image_ready and text_ready

    summary = {
        "task": "M0-05",
        "grok": which("grok"),
        "grok_version": subprocess.run(
            ["grok", "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
        or subprocess.run(
            ["grok", "--version"], capture_output=True, text=True, check=False
        ).stderr.strip(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "accepted": accepted,
        "acceptance_notes": {
            "required": ["text.structured_generate ready", "image.generate ready + decode"],
            "optional_for_v1": ["image.edit", "video.image_to_video"],
            "video_may_degrade": True,
        },
        "capabilities": [asdict(item) for item in results],
        "command_contract": {
            "text": "grok -p --json-schema --output-format json",
            "image_generate": "grok -p --tools image_gen --yolo --output-format json",
            "image_edit": "grok -p --tools image_edit --yolo --output-format json",
            "image_to_video": "grok -p --tools image_to_video --yolo --output-format json",
            "success_criterion": "copy tool output into task dir and decode with PIL/ffprobe",
        },
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"accepted": accepted, "capabilities": summary["capabilities"]}, indent=2, ensure_ascii=False))
    print(f"summary: {summary_path}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
