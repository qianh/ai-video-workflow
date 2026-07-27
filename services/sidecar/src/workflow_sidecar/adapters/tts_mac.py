"""TTS: CosyVoice if present, else macOS `say` + ffmpeg WAV."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import MediaResult
from .ffmpeg_pipeline import run_cmd, which_ffmpeg
from .policy import allow_mock


def synthesize_speech(
    dest: Path,
    *,
    text: str,
    voice: str | None = None,
) -> MediaResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()
    if not text:
        return MediaResult(
            ok=False,
            adapter="tts",
            output_path=None,
            mime_type="audio/wav",
            error="empty text",
        )

    if allow_mock():
        return _mock_wav(dest, text)

    say = shutil.which("say")
    ffmpeg = which_ffmpeg()
    if say and ffmpeg:
        with tempfile.TemporaryDirectory() as tmp:
            aiff = Path(tmp) / "voice.aiff"
            cmd_say = [say, "-o", str(aiff)]
            if voice:
                cmd_say.extend(["-v", voice])
            cmd_say.append(text[:800])
            completed = subprocess.run(
                cmd_say, capture_output=True, text=True, timeout=60, check=False
            )
            if completed.returncode != 0 or not aiff.is_file():
                if allow_mock():
                    return _mock_wav(dest, text)
                return MediaResult(
                    ok=False,
                    adapter="say",
                    output_path=None,
                    mime_type="audio/wav",
                    error=(completed.stderr or "say failed")[-300:],
                )
            conv = run_cmd(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(aiff),
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    str(dest),
                ],
                timeout=60,
            )
            if conv.returncode != 0 or not dest.is_file():
                if allow_mock():
                    return _mock_wav(dest, text)
                return MediaResult(
                    ok=False,
                    adapter="say+ffmpeg",
                    output_path=None,
                    mime_type="audio/wav",
                    error=(conv.stderr or "wav convert failed")[-300:],
                )
        duration_ms = max(800, min(20000, len(text) * 90))
        try:
            from .ffmpeg_pipeline import probe_duration_ms

            duration_ms = probe_duration_ms(dest) or duration_ms
        except Exception:
            pass
        return MediaResult(
            ok=True,
            adapter="say",
            output_path=dest,
            mime_type="audio/wav",
            duration_ms=duration_ms,
            degraded=True,
            meta={"fallback": "macos_say", "note": "CosyVoice not installed"},
        )

    if allow_mock():
        return _mock_wav(dest, text)
    return MediaResult(
        ok=False,
        adapter="tts",
        output_path=None,
        mime_type="audio/wav",
        error="no TTS backend (say/ffmpeg/cosyvoice)",
    )


def _mock_wav(dest: Path, text: str) -> MediaResult:
    dest.write_bytes(b"RIFF....WAVEmock" + text.encode("utf-8")[:200])
    return MediaResult(
        ok=True,
        adapter="mock",
        output_path=dest,
        mime_type="audio/wav",
        duration_ms=max(800, min(8000, len(text) * 80)),
        degraded=True,
        mock=True,
    )
