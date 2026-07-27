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

    # Prefer registered CosyVoice3 when ready (ADR-007).
    cosy = _try_cosyvoice(dest, text=text, voice=voice)
    if cosy is not None:
        return cosy

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


def _try_cosyvoice(
    dest: Path, *, text: str, voice: str | None
) -> MediaResult | None:
    """Invoke CosyVoice CLI if registered. Contract:

    WORKFLOW_COSYVOICE_BIN <out_wav> --text "..." [--voice name]
    Exit 0 and write dest path.
    """
    try:
        from .components import cosyvoice_binary
    except Exception:
        return None
    binary = cosyvoice_binary()
    if not binary:
        return None
    cmd = [binary, str(dest), "--text", text[:2000]]
    if voice:
        cmd.extend(["--voice", voice])
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
    except Exception as exc:
        return MediaResult(
            ok=False,
            adapter="cosyvoice3",
            output_path=None,
            mime_type="audio/wav",
            error=str(exc)[:400],
        )
    if completed.returncode != 0 or not dest.is_file():
        return MediaResult(
            ok=False,
            adapter="cosyvoice3",
            output_path=None,
            mime_type="audio/wav",
            error=(completed.stderr or "cosyvoice failed")[-400:],
        )
    duration_ms = max(800, min(30000, len(text) * 80))
    try:
        from .ffmpeg_pipeline import probe_duration_ms

        duration_ms = probe_duration_ms(dest) or duration_ms
    except Exception:
        pass
    return MediaResult(
        ok=True,
        adapter="cosyvoice3",
        output_path=dest,
        mime_type="audio/wav",
        duration_ms=duration_ms,
        meta={"backend": "cosyvoice3"},
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
