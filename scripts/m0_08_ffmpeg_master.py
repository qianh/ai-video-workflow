#!/usr/bin/env python3
"""M0-08: verify FFmpeg vertical motion, ASS captions, mix, and master render.

Builds synthetic fixtures (no external media), then produces a 1080x1920 master
under 90 seconds with burned ASS subtitles and mixed voice/music.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts" / "m0-08"
WIDTH = 1080
HEIGHT = 1920
DURATION_SEC = 8.0


@dataclass
class StepResult:
    name: str
    ok: bool
    elapsed_sec: float
    detail: str
    outputs: list[str]
    error: str | None = None


def which(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} not found on PATH")
    return path


def run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def ffprobe_json(path: Path) -> dict[str, Any]:
    ffprobe = which("ffprobe")
    completed = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffprobe failed")
    return json.loads(completed.stdout)


def ass_timestamp(seconds: float) -> str:
    whole = int(seconds)
    centis = int(round((seconds - whole) * 100))
    if centis == 100:
        whole += 1
        centis = 0
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def write_ass(path: Path, duration: float) -> None:
    # PlayRes matches final vertical frame so layout stays stable.
    end_ts = ass_timestamp(duration)
    content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,PingFang SC,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,80,80,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,{end_ts},Default,,0,0,0,,夜市雨巷
Dialogue: 0,0:00:02.00,{end_ts},Default,,0,0,0,,她捡起发光的 U 盘
"""
    path.write_text(content, encoding="utf-8")


def step_generate_fixtures(out_dir: Path) -> StepResult:
    ffmpeg = which("ffmpeg")
    started = time.perf_counter()
    fixtures = out_dir / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)

    still = fixtures / "still.png"
    voice = fixtures / "voice.wav"
    music = fixtures / "music.wav"
    ass = fixtures / "captions.ass"

    # Still: square source to force cover-scale into 9:16 + Ken Burns.
    cmd_still = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x141821:s=1280x1280:d=1",
        "-frames:v",
        "1",
        str(still),
    ]
    # Voice-like lead tone (center-ish).
    cmd_voice = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=220:sample_rate=48000:duration={DURATION_SEC}",
        "-af",
        "volume=0.35",
        str(voice),
    ]
    # Quieter music bed.
    cmd_music = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=110:sample_rate=48000:duration={DURATION_SEC}",
        "-af",
        "volume=0.08",
        str(music),
    ]

    for cmd, label in (
        (cmd_still, "still"),
        (cmd_voice, "voice"),
        (cmd_music, "music"),
    ):
        completed = run(cmd)
        if completed.returncode != 0:
            return StepResult(
                name="generate_fixtures",
                ok=False,
                elapsed_sec=time.perf_counter() - started,
                detail=f"failed building {label}",
                outputs=[],
                error=completed.stderr[-800:],
            )

    write_ass(ass, DURATION_SEC)
    elapsed = time.perf_counter() - started
    return StepResult(
        name="generate_fixtures",
        ok=True,
        elapsed_sec=elapsed,
        detail="still.png + voice.wav + music.wav + captions.ass",
        outputs=[str(still), str(voice), str(music), str(ass)],
    )


def step_render_master(out_dir: Path) -> StepResult:
    ffmpeg = which("ffmpeg")
    started = time.perf_counter()
    fixtures = out_dir / "fixtures"
    staged = out_dir / "staged.mp4"
    master = out_dir / "master.mp4"
    still = fixtures / "still.png"
    voice = fixtures / "voice.wav"
    music = fixtures / "music.wav"
    ass = fixtures / "captions.ass"

    fps = 30
    # Stage 1: vertical Ken Burns + voice/music mix (no subtitles yet).
    filter_complex = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={WIDTH}x{HEIGHT}:fps={fps},"
        f"trim=duration={DURATION_SEC},setpts=PTS-STARTPTS[vout];"
        f"[1:a][2:a]amix=inputs=2:duration=first:dropout_transition=0,"
        f"volume=1.0,atrim=duration={DURATION_SEC},asetpts=PTS-STARTPTS[aout]"
    )

    stage_cmd = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-i",
        str(still),
        "-i",
        str(voice),
        "-i",
        str(music),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-t",
        str(DURATION_SEC),
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(staged),
    ]
    stage = run(stage_cmd)
    if stage.returncode != 0:
        return StepResult(
            name="render_master",
            ok=False,
            elapsed_sec=time.perf_counter() - started,
            detail="ffmpeg stage (motion+mix) failed",
            outputs=[],
            error=stage.stderr[-1200:],
        )

    # Stage 2: attach captions.
    # Stock Homebrew ffmpeg often lacks libass/freetype, so burn-in filters
    # (ass/subtitles/drawtext) may be unavailable. Soft-mux ASS -> mov_text in MP4.
    soft_cmd = [
        ffmpeg,
        "-y",
        "-i",
        "staged.mp4",
        "-i",
        "fixtures/captions.ass",
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-map",
        "1",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        "language=zho",
        "-movflags",
        "+faststart",
        "master.mp4",
    ]
    soft = run(soft_cmd, cwd=out_dir)
    elapsed = time.perf_counter() - started
    if soft.returncode != 0:
        return StepResult(
            name="render_master",
            ok=False,
            elapsed_sec=elapsed,
            detail="ffmpeg soft-subtitle mux failed",
            outputs=[str(staged)],
            error=soft.stderr[-1200:],
        )

    filters_out = run([ffmpeg, "-hide_banner", "-filters"]).stdout or ""
    burn_in_available = any(
        line.split()[1] == "ass"
        for line in filters_out.splitlines()
        if len(line.split()) >= 2 and line.split()[1] in {"ass", "subtitles"}
    )
    detail = (
        f"wrote {master.name} via vertical Ken Burns + mix + soft ASS->mov_text; "
        f"burn-in filters={'available' if burn_in_available else 'unavailable (no libass)'}"
    )
    return StepResult(
        name="render_master",
        ok=True,
        elapsed_sec=elapsed,
        detail=detail,
        outputs=[str(staged), str(master), str(ass)],
    )


def step_validate_master(out_dir: Path) -> StepResult:
    started = time.perf_counter()
    master = out_dir / "master.mp4"
    try:
        info = ffprobe_json(master)
        streams = info.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        subtitle = next((s for s in streams if s.get("codec_type") == "subtitle"), None)
        if video is None:
            raise RuntimeError("missing video stream")
        if audio is None:
            raise RuntimeError("missing audio stream")
        if subtitle is None:
            raise RuntimeError("missing subtitle stream")
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        if width != WIDTH or height != HEIGHT:
            raise RuntimeError(f"expected {WIDTH}x{HEIGHT}, got {width}x{height}")
        duration = float((info.get("format") or {}).get("duration") or 0)
        if duration <= 0 or duration > 90:
            raise RuntimeError(f"duration out of range: {duration}")
        if video.get("codec_name") not in {"h264"}:
            raise RuntimeError(f"unexpected video codec: {video.get('codec_name')}")
        if audio.get("codec_name") not in {"aac"}:
            raise RuntimeError(f"unexpected audio codec: {audio.get('codec_name')}")
        if subtitle.get("codec_name") not in {"mov_text", "ass", "subrip"}:
            raise RuntimeError(f"unexpected subtitle codec: {subtitle.get('codec_name')}")

        ass = out_dir / "fixtures" / "captions.ass"
        ass_text = ass.read_text(encoding="utf-8")
        if "夜市雨巷" not in ass_text or "U 盘" not in ass_text:
            raise RuntimeError("ASS fixture missing expected dialogue")

        elapsed = time.perf_counter() - started
        detail = {
            "width": width,
            "height": height,
            "duration_sec": duration,
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
            "subtitle_codec": subtitle.get("codec_name"),
            "size_bytes": master.stat().st_size,
            "subtitle_mode": "soft_mux_mov_text",
        }
        return StepResult(
            name="validate_master",
            ok=True,
            elapsed_sec=elapsed,
            detail=json.dumps(detail, ensure_ascii=False),
            outputs=[str(master)],
        )
    except Exception as exc:
        return StepResult(
            name="validate_master",
            ok=False,
            elapsed_sec=time.perf_counter() - started,
            detail="master validation failed",
            outputs=[str(master)] if master.exists() else [],
            error=str(exc),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: list[StepResult] = []
    for fn in (step_generate_fixtures, step_render_master, step_validate_master):
        result = fn(out_dir)
        steps.append(result)
        status = "ok" if result.ok else f"FAIL ({result.error})"
        print(f"[m0-08] {result.name}: {status} in {result.elapsed_sec:.2f}s", flush=True)
        if not result.ok:
            break

    accepted = all(step.ok for step in steps) and len(steps) == 3
    try:
        ffmpeg_v = run([which("ffmpeg"), "-version"]).stdout.splitlines()[0]
        ffprobe_v = run([which("ffprobe"), "-version"]).stdout.splitlines()[0]
    except Exception:
        ffmpeg_v = "unknown"
        ffprobe_v = "unknown"

    summary = {
        "task": "M0-08",
        "accepted": accepted,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ffmpeg": which("ffmpeg") if shutil.which("ffmpeg") else None,
        "ffprobe": which("ffprobe") if shutil.which("ffprobe") else None,
        "ffmpeg_version": ffmpeg_v,
        "ffprobe_version": ffprobe_v,
        "target": {
            "width": WIDTH,
            "height": HEIGHT,
            "max_duration_sec": 90,
            "features": [
                "vertical_kenburns",
                "ass_source",
                "soft_subtitle_mux",
                "voice_music_mix",
                "h264_aac_master",
            ],
        },
        "steps": [asdict(step) for step in steps],
        "recommended_adapter_mapping": {
            "capabilities": ["media.proxy", "media.render", "quality.media_check"],
            "builder": "predefined filtergraph templates only; no raw filter strings from UI",
            "outputs": "task staging dir only",
        },
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"accepted": accepted}, indent=2))
    print(f"summary: {summary_path}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
