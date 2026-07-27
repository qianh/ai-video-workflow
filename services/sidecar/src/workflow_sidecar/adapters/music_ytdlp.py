"""Music download via yt-dlp (M0-09 production path)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import MediaResult
from .ffmpeg_pipeline import run_cmd, which_ffmpeg
from .policy import allow_mock


def which_ytdlp() -> str | None:
    return shutil.which("yt-dlp") or shutil.which("music-downloader")


def download_audio(
    dest: Path,
    *,
    url: str | None = None,
    title: str = "bgm",
) -> MediaResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if allow_mock() and not url:
        dest.write_bytes(f"mock-music:{title}".encode("utf-8"))
        return MediaResult(
            ok=True,
            adapter="mock",
            output_path=dest,
            mime_type="audio/mpeg",
            degraded=True,
            mock=True,
            meta={"title": title, "url": url},
        )
    ytdlp = which_ytdlp()
    ffmpeg = which_ffmpeg()

    if url and ytdlp and "yt-dlp" in Path(ytdlp).name:
        out_template = str(dest.with_suffix("")) + ".%(ext)s"
        cmd = [
            ytdlp,
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "-o",
            out_template,
            "--no-playlist",
            url,
        ]
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, check=False
        )
        candidates = list(dest.parent.glob(dest.stem + ".*"))
        produced = next(
            (
                p
                for p in candidates
                if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".opus"}
            ),
            None,
        )
        if completed.returncode == 0 and produced and produced.is_file():
            if produced != dest:
                shutil.copy2(produced, dest)
            return MediaResult(
                ok=True,
                adapter="yt-dlp",
                output_path=dest if dest.is_file() else produced,
                mime_type="audio/mpeg",
                meta={"url": url, "title": title},
            )
        if not allow_mock():
            return MediaResult(
                ok=False,
                adapter="yt-dlp",
                output_path=None,
                mime_type="audio/mpeg",
                error=(completed.stderr or "yt-dlp failed")[-500:],
            )

    # No URL or download failed: generate short real bed with ffmpeg sine
    if ffmpeg:
        wav = dest.with_suffix(".wav") if dest.suffix.lower() != ".wav" else dest
        completed = run_cmd(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=110:sample_rate=48000:duration=4",
                "-af",
                "volume=0.12",
                str(wav),
            ],
            timeout=30,
        )
        if completed.returncode == 0 and wav.is_file():
            if dest.suffix.lower() != ".wav" and wav != dest:
                shutil.copy2(wav, dest)
                out = dest
            else:
                out = wav
            return MediaResult(
                ok=True,
                adapter="ffmpeg",
                output_path=out,
                mime_type="audio/wav",
                duration_ms=4000,
                degraded=True,
                meta={
                    "kind": "synthetic_bed",
                    "title": title,
                    "url": url,
                    "note": "generated sine bed (no yt-dlp URL)",
                },
            )

    if allow_mock():
        dest.write_bytes(f"mock-music:{title}".encode("utf-8"))
        return MediaResult(
            ok=True,
            adapter="mock",
            output_path=dest,
            mime_type="audio/mpeg",
            degraded=True,
            mock=True,
            meta={"title": title, "url": url},
        )
    return MediaResult(
        ok=False,
        adapter="music",
        output_path=None,
        mime_type="audio/mpeg",
        error="music download unavailable",
    )
