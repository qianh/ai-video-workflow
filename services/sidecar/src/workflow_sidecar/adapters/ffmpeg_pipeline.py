"""FFmpeg production templates: static motion, mix, soft captions, export."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .base import MediaResult
from .policy import allow_mock, mock_reason

WIDTH = 1080
HEIGHT = 1920


def which_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def which_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def run_cmd(cmd: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def ffprobe(path: Path) -> dict[str, Any]:
    probe = which_ffprobe()
    if not probe:
        raise RuntimeError("ffprobe not found")
    completed = run_cmd(
        [
            probe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-400:] or "ffprobe failed")
    return json.loads(completed.stdout)


def probe_duration_ms(path: Path) -> int:
    try:
        info = ffprobe(path)
        duration = float(info.get("format", {}).get("duration") or 0)
        return max(0, int(duration * 1000))
    except Exception:
        return 0


def write_placeholder_jpeg(dest: Path, *, color: str = "0x2a3344") -> MediaResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if allow_mock():
        dest.write_bytes(
            bytes.fromhex(
                "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
                "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
                "1c2837292c30313434341f27393d383232ffc00011080001000103011100"
                "021101031101ffc40014000100000000000000000000000000000000ffc400141001"
                "00000000000000000000000000000000ffda000c0301000210031000003f00bf80ffd9"
            )
        )
        return MediaResult(
            ok=True,
            adapter="mock",
            output_path=dest,
            mime_type="image/jpeg",
            width=1,
            height=1,
            degraded=True,
            mock=True,
            meta={"kind": "minimal_jpeg", "reason": mock_reason()},
        )
    ffmpeg = which_ffmpeg()
    out = dest if dest.suffix.lower() in {".jpg", ".jpeg", ".png"} else dest.with_suffix(".jpg")
    if ffmpeg:
        completed = run_cmd(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=768x1024:d=1",
                "-frames:v",
                "1",
                str(out),
            ],
            timeout=30,
        )
        if completed.returncode == 0 and out.is_file() and out.stat().st_size > 100:
            if out != dest:
                shutil.copy2(out, dest)
            return MediaResult(
                ok=True,
                adapter="ffmpeg",
                output_path=dest,
                mime_type="image/jpeg",
                width=768,
                height=1024,
                degraded=True,
                mock=False,
                meta={"kind": "color_still", "color": color},
            )
    # minimal JPEG bytes
    dest.write_bytes(
        bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
            "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
            "1c2837292c30313434341f27393d383232ffc00011080001000103011100"
            "021101031101ffc40014000100000000000000000000000000000000ffc400141001"
            "00000000000000000000000000000000ffda000c0301000210031000003f00bf80ffd9"
        )
    )
    return MediaResult(
        ok=True,
        adapter="mock",
        output_path=dest,
        mime_type="image/jpeg",
        width=1,
        height=1,
        degraded=True,
        mock=True,
        meta={"kind": "minimal_jpeg", "reason": mock_reason()},
    )


def static_motion_from_image(
    image_path: Path,
    dest: Path,
    *,
    duration_sec: float = 2.0,
    fps: int = 24,
) -> MediaResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if allow_mock():
        dest.write_bytes(b"mock-static-motion")
        return MediaResult(
            ok=True,
            adapter="mock",
            output_path=dest,
            mime_type="video/mp4",
            duration_ms=int(duration_sec * 1000),
            degraded=True,
            mock=True,
            meta={"reason": "allow_mock"},
        )
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        if allow_mock():
            dest.write_bytes(b"mock-static-motion")
            return MediaResult(
                ok=True,
                adapter="mock",
                output_path=dest,
                mime_type="video/mp4",
                duration_ms=int(duration_sec * 1000),
                degraded=True,
                mock=True,
                meta={"reason": "ffmpeg_missing"},
            )
        return MediaResult(
            ok=False,
            adapter="ffmpeg",
            output_path=None,
            mime_type="video/mp4",
            error="ffmpeg not found and mock not allowed",
        )
    if not image_path.is_file():
        return MediaResult(
            ok=False,
            adapter="ffmpeg",
            output_path=None,
            mime_type="video/mp4",
            error=f"image missing: {image_path}",
        )
    duration_sec = max(0.5, min(8.0, duration_sec))
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"zoompan=z='min(zoom+0.0012,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={WIDTH}x{HEIGHT}:fps={fps},"
        f"trim=duration={duration_sec},setpts=PTS-STARTPTS,"
        f"format=yuv420p"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=48000:cl=stereo:d={duration_sec}",
        "-vf",
        vf,
        "-t",
        str(duration_sec),
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-shortest",
        str(dest),
    ]
    started = time.perf_counter()
    completed = run_cmd(cmd, timeout=120)
    if completed.returncode != 0 or not dest.is_file():
        return MediaResult(
            ok=False,
            adapter="ffmpeg",
            output_path=None,
            mime_type="video/mp4",
            error=(completed.stderr or "static_motion failed")[-800:],
            meta={"elapsed_sec": round(time.perf_counter() - started, 3)},
        )
    return MediaResult(
        ok=True,
        adapter="ffmpeg",
        output_path=dest,
        mime_type="video/mp4",
        duration_ms=probe_duration_ms(dest) or int(duration_sec * 1000),
        width=WIDTH,
        height=HEIGHT,
        meta={
            "template": "static_motion_kenburns",
            "elapsed_sec": round(time.perf_counter() - started, 3),
        },
    )


def mux_still_with_audio(
    image_path: Path | None,
    audio_path: Path | None,
    dest: Path,
    *,
    duration_sec: float = 2.0,
) -> MediaResult:
    """Simplified lipsync: still/frame + dialogue audio (no mouth animation)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if allow_mock():
        dest.write_bytes(b"mock-lipsync-simplified")
        return MediaResult(
            ok=True,
            adapter="mock",
            output_path=dest,
            mime_type="video/mp4",
            duration_ms=int(duration_sec * 1000),
            degraded=True,
            mock=True,
            meta={"level": "simplified"},
        )
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        if allow_mock():
            dest.write_bytes(b"mock-lipsync-simplified")
            return MediaResult(
                ok=True,
                adapter="mock",
                output_path=dest,
                mime_type="video/mp4",
                duration_ms=int(duration_sec * 1000),
                degraded=True,
                mock=True,
                meta={"level": "simplified"},
            )
        return MediaResult(
            ok=False,
            adapter="ffmpeg",
            output_path=None,
            mime_type="video/mp4",
            error="ffmpeg missing for simplified lipsync",
        )
    inputs: list[str] = []
    if image_path and image_path.is_file():
        inputs.extend(["-loop", "1", "-i", str(image_path)])
        vfilter = (
            f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},format=yuv420p,trim=duration={duration_sec},"
            f"setpts=PTS-STARTPTS[v]"
        )
        vmap = "[v]"
        a_index = 1
        use_fc = True
    else:
        inputs.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={WIDTH}x{HEIGHT}:d={duration_sec}",
            ]
        )
        vfilter = ""
        vmap = "0:v"
        a_index = 1
        use_fc = False
    if audio_path and audio_path.is_file():
        inputs.extend(["-i", str(audio_path)])
        amap = f"{a_index}:a"
    else:
        inputs.extend(
            ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration_sec}"]
        )
        amap = f"{a_index}:a"
    cmd = [ffmpeg, "-y", *inputs]
    if use_fc:
        cmd.extend(["-filter_complex", vfilter, "-map", vmap, "-map", amap])
    else:
        cmd.extend(["-map", "0:v", "-map", "1:a"])
    cmd.extend(
        [
            "-t",
            str(duration_sec),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-shortest",
            str(dest),
        ]
    )
    completed = run_cmd(cmd, timeout=120)
    if completed.returncode != 0 or not dest.is_file():
        return MediaResult(
            ok=False,
            adapter="ffmpeg",
            output_path=None,
            mime_type="video/mp4",
            error=(completed.stderr or "mux failed")[-600:],
        )
    return MediaResult(
        ok=True,
        adapter="ffmpeg",
        output_path=dest,
        mime_type="video/mp4",
        duration_ms=probe_duration_ms(dest) or int(duration_sec * 1000),
        width=WIDTH,
        height=HEIGHT,
        degraded=True,
        meta={"level": "simplified", "note": "still+audio mux, not MuseTalk"},
    )


def render_timeline_media(
    dest: Path,
    *,
    stills: list[Path],
    voice: Path | None = None,
    music: Path | None = None,
    ass_path: Path | None = None,
    duration_sec: float = 3.0,
    kind: str = "proxy",
) -> MediaResult:
    """Compile a vertical master from stills + optional audio + soft ASS."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if allow_mock():
        dest.write_bytes(f"mock-render-{kind}".encode("utf-8"))
        return MediaResult(
            ok=True,
            adapter="mock",
            output_path=dest,
            mime_type="video/mp4",
            duration_ms=int(duration_sec * 1000),
            degraded=True,
            mock=True,
        )
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        if allow_mock():
            dest.write_bytes(f"mock-render-{kind}".encode("utf-8"))
            return MediaResult(
                ok=True,
                adapter="mock",
                output_path=dest,
                mime_type="video/mp4",
                duration_ms=int(duration_sec * 1000),
                degraded=True,
                mock=True,
            )
        return MediaResult(
            ok=False,
            adapter="ffmpeg",
            output_path=None,
            mime_type="video/mp4",
            error="ffmpeg not found",
        )

    duration_sec = max(1.0, min(12.0, duration_sec))
    still = next((p for p in stills if p.is_file()), None)
    preset = "ultrafast" if kind == "proxy" else "veryfast"
    crf = "28" if kind == "proxy" else "20"
    fps = 24 if kind == "proxy" else 30

    staged = dest.with_suffix(".staged.mp4")
    if still:
        vf = (
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},"
            f"zoompan=z='min(zoom+0.0008,1.1)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={WIDTH}x{HEIGHT}:fps={fps},"
            f"trim=duration={duration_sec},setpts=PTS-STARTPTS,format=yuv420p"
        )
        v_inputs = ["-loop", "1", "-i", str(still)]
        filter_complex = f"[0:v]{vf}[vout]"
        maps = ["-map", "[vout]"]
        next_idx = 1
    else:
        v_inputs = [
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x141821:s={WIDTH}x{HEIGHT}:d={duration_sec}",
        ]
        filter_complex = ""
        maps = ["-map", "0:v"]
        next_idx = 1

    a_inputs: list[str] = []
    audio_labels: list[str] = []
    if voice and voice.is_file():
        a_inputs.extend(["-i", str(voice)])
        audio_labels.append(f"[{next_idx}:a]")
        next_idx += 1
    if music and music.is_file():
        a_inputs.extend(["-i", str(music)])
        audio_labels.append(f"[{next_idx}:a]")
        next_idx += 1
    if not audio_labels:
        a_inputs.extend(
            ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration_sec}"]
        )
        audio_labels.append(f"[{next_idx}:a]")

    if len(audio_labels) == 1:
        a_map = audio_labels[0].strip("[]")
        maps.extend(["-map", a_map])
        fc = filter_complex if filter_complex else None
    else:
        mix_in = "".join(audio_labels)
        a_fc = (
            f"{mix_in}amix=inputs={len(audio_labels)}:duration=first:"
            f"dropout_transition=0,volume=1.0,atrim=duration={duration_sec},"
            f"asetpts=PTS-STARTPTS[aout]"
        )
        fc = f"{filter_complex};{a_fc}" if filter_complex else a_fc
        maps.extend(["-map", "[aout]"])

    target = staged if ass_path and ass_path.is_file() else dest
    cmd = [ffmpeg, "-y", *v_inputs, *a_inputs]
    if fc:
        cmd.extend(["-filter_complex", fc])
    cmd.extend(
        [
            *maps,
            "-t",
            str(duration_sec),
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            preset,
            "-crf",
            crf,
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(target),
        ]
    )
    completed = run_cmd(cmd, timeout=180)
    if completed.returncode != 0 or not target.is_file():
        return MediaResult(
            ok=False,
            adapter="ffmpeg",
            output_path=None,
            mime_type="video/mp4",
            error=(completed.stderr or "render failed")[-1000:],
        )

    if ass_path and ass_path.is_file() and staged.is_file():
        soft = run_cmd(
            [
                ffmpeg,
                "-y",
                "-i",
                str(staged),
                "-i",
                str(ass_path),
                "-map",
                "0:v",
                "-map",
                "0:a?",
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
                str(dest),
            ],
            timeout=60,
        )
        if soft.returncode != 0 or not dest.is_file():
            shutil.copy2(staged, dest)
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
    elif staged.is_file() and staged != dest:
        shutil.move(str(staged), str(dest))

    return MediaResult(
        ok=True,
        adapter="ffmpeg",
        output_path=dest,
        mime_type="video/mp4",
        duration_ms=probe_duration_ms(dest) or int(duration_sec * 1000),
        width=WIDTH,
        height=HEIGHT,
        meta={"kind": kind, "template": "timeline_kenburns_mix", "has_ass": bool(ass_path)},
    )


def export_profile_copy(
    src: Path,
    dest: Path,
    *,
    profile: str,
) -> MediaResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = which_ffmpeg()
    if not src.is_file():
        if allow_mock():
            dest.write_bytes(f"export-{profile}".encode("utf-8"))
            return MediaResult(
                ok=True,
                adapter="mock",
                output_path=dest,
                mime_type="video/mp4",
                mock=True,
                degraded=True,
            )
        return MediaResult(
            ok=False,
            adapter="ffmpeg",
            output_path=None,
            mime_type="video/mp4",
            error="source missing for export",
        )
    if not ffmpeg:
        shutil.copy2(src, dest)
        return MediaResult(
            ok=True,
            adapter="copy",
            output_path=dest,
            mime_type="video/mp4",
            duration_ms=probe_duration_ms(dest),
            degraded=True,
        )
    bitrate = {"master": "4M", "douyin": "3M", "hongguo": "2.5M"}.get(profile, "3M")
    completed = run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            bitrate,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        timeout=180,
    )
    if completed.returncode != 0 or not dest.is_file():
        shutil.copy2(src, dest)
    return MediaResult(
        ok=True,
        adapter="ffmpeg",
        output_path=dest,
        mime_type="video/mp4",
        duration_ms=probe_duration_ms(dest),
        meta={"profile": profile, "bitrate": bitrate},
    )
