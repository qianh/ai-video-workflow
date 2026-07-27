"""Grok CLI image generation (M0-05 / identity pack contract)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .base import MediaResult
from .ffmpeg_pipeline import write_placeholder_jpeg
from .policy import allow_mock


def which_grok() -> str | None:
    return shutil.which("grok")


def generate_image(
    dest: Path,
    *,
    prompt: str,
    negative_prompt: str | None = None,
    timeout: int = 180,
) -> MediaResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Tests/CI mock mode: never invoke Grok (auth/cost/latency).
    if allow_mock():
        result = write_placeholder_jpeg(dest)
        result.meta["fallback"] = "allow_mock"
        result.mock = True
        result.adapter = "mock"
        return result
    grok = which_grok()
    if not grok:
        return MediaResult(
            ok=False,
            adapter="grok",
            output_path=None,
            mime_type="image/jpeg",
            error="grok CLI not found",
        )

    schema = {
        "type": "object",
        "required": ["image_path", "ok"],
        "properties": {
            "image_path": {"type": "string"},
            "ok": {"type": "boolean"},
        },
    }
    neg = f" Avoid: {negative_prompt}." if negative_prompt else ""
    full_prompt = (
        "Use image_gen to create one production still image. "
        f"Subject: {prompt}.{neg} "
        "Report the absolute image path as image_path and ok=true."
    )
    command = [
        grok,
        "-p",
        full_prompt,
        "--yolo",
        "--tools",
        "image_gen",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
        "--max-turns",
        "3",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
            check=False,
        )
    except Exception as exc:
        if allow_mock():
            result = write_placeholder_jpeg(dest)
            result.meta["grok_error"] = str(exc)[:400]
            result.degraded = True
            result.mock = True
            return result
        return MediaResult(
            ok=False,
            adapter="grok",
            output_path=None,
            mime_type="image/jpeg",
            error=str(exc)[:500],
        )

    if completed.returncode != 0:
        if allow_mock():
            result = write_placeholder_jpeg(dest)
            result.meta["grok_stderr"] = (completed.stderr or "")[-300:]
            result.degraded = True
            result.mock = True
            return result
        return MediaResult(
            ok=False,
            adapter="grok",
            output_path=None,
            mime_type="image/jpeg",
            error=f"grok exit {completed.returncode}: {(completed.stderr or '')[-400:]}",
        )
    try:
        payload = json.loads(completed.stdout)
        structured = payload.get("structuredOutput") or payload
        if isinstance(structured, str):
            structured = json.loads(structured)
        image_path = structured.get("image_path")
        if not image_path or not Path(image_path).is_file():
            raise RuntimeError("grok did not return image_path")
        shutil.copy2(image_path, dest)
    except Exception as exc:
        if allow_mock():
            result = write_placeholder_jpeg(dest)
            result.meta["parse_error"] = str(exc)[:300]
            result.degraded = True
            result.mock = True
            return result
        return MediaResult(
            ok=False,
            adapter="grok",
            output_path=None,
            mime_type="image/jpeg",
            error=str(exc)[:500],
        )

    width = height = None
    try:
        from PIL import Image

        with Image.open(dest) as image:
            width, height = image.size
    except Exception:
        pass
    return MediaResult(
        ok=True,
        adapter="grok",
        output_path=dest,
        mime_type="image/jpeg",
        width=width,
        height=height,
        meta={"tool": "image_gen", "provider": "grok"},
    )
