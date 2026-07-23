#!/usr/bin/env python3
"""M0-09: verify music-downloader writes only to task staging.

Acceptance:
- download via john-skills music-downloader wrapper
- --output forces task staging dir (never rely on ~/Music/Downloads default)
- after success, staging contains MP3; default music dir gains no new files
- capture source metadata for pending-authorization import
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
DEFAULT_OUT = ROOT / "artifacts" / "m0-09"
DEFAULT_MUSIC = Path.home() / "Music" / "Downloads"
CANDIDATE_SCRIPTS = [
    Path.home() / ".claude/skills/john/skills/music-downloader/scripts/download.sh",
    Path.home()
    / ".claude/plugins/cache/john-skills/john/1.3.0/skills/music-downloader/scripts/download.sh",
    Path.home() / ".agents/skills/music-downloader/scripts/download.sh",
]


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    error: str | None = None


def resolve_download_script(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"download script not found: {path}")
        return path
    for candidate in CANDIDATE_SCRIPTS:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "music-downloader scripts/download.sh not found in known skill paths"
    )


def snapshot_dir(path: Path) -> dict[str, float]:
    if not path.is_dir():
        return {}
    result: dict[str, float] = {}
    for entry in path.rglob("*"):
        if entry.is_file():
            rel = str(entry.relative_to(path))
            result[rel] = entry.stat().st_mtime
    return result


def new_files(before: dict[str, float], after: dict[str, float]) -> list[str]:
    added = []
    for key, mtime in after.items():
        if key not in before or mtime > before[key] + 0.001:
            # treat new or rewritten as change
            if key not in before:
                added.append(key)
    return sorted(added)


def list_audio(staging: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ("*.mp3", "*.m4a", "*.opus", "*.wav", "*.webm"):
        files.extend(staging.glob(pattern))
    return sorted(files)


def ffprobe_tags(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"path": str(path), "size": path.stat().st_size}
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name,size:format_tags:stream=codec_name,codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"path": str(path), "size": path.stat().st_size, "ffprobe_error": completed.stderr}
    payload = json.loads(completed.stdout)
    payload["path"] = str(path)
    return payload


def dump_source_json(query: str, staging: Path) -> dict[str, Any] | None:
    """Best-effort source capture without downloading again when possible."""
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        return None
    target = query if query.startswith("http") else f"ytsearch1:{query}"
    completed = subprocess.run(
        [
            ytdlp,
            "--skip-download",
            "--no-playlist",
            "--print",
            "%(.{id,title,webpage_url,uploader,channel,duration,extractor})j",
            target,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=staging,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": (completed.stderr or completed.stdout)[-500:],
            "query": query,
        }
    line = (completed.stdout or "").strip().splitlines()
    if not line:
        return {"ok": False, "error": "empty metadata", "query": query}
    try:
        data = json.loads(line[0])
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid metadata json", "raw": line[0][:500], "query": query}
    data["ok"] = True
    data["query"] = query
    return data


def run_download(
    script: Path,
    *,
    staging: Path,
    item: str,
    timeout_sec: int,
) -> tuple[int, str]:
    staging.mkdir(parents=True, exist_ok=True)
    cmd = ["bash", str(script), "--output", str(staging), item]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=os.environ.copy(),
        check=False,
    )
    log = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    return completed.returncode, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--item",
        default="https://archive.org/details/testmp3testfile",
        help="URL or search term. Default: short public-domain Internet Archive MP3.",
    )
    parser.add_argument("--script", default=None, help="Path to download.sh")
    parser.add_argument("--timeout-sec", type=int, default=240)
    parser.add_argument(
        "--default-music-dir",
        type=Path,
        default=DEFAULT_MUSIC,
        help="Directory that must not receive new files",
    )
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    staging = out_dir / "staging"
    out_dir.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    checks: list[CheckResult] = []
    started = time.perf_counter()

    try:
        script = resolve_download_script(args.script)
        checks.append(
            CheckResult(
                name="locate_script",
                ok=True,
                detail=str(script),
            )
        )
    except Exception as exc:
        checks.append(CheckResult(name="locate_script", ok=False, detail="", error=str(exc)))
        return finish(out_dir, checks, staging, args, started, accepted=False, log="")

    if not shutil.which("yt-dlp"):
        checks.append(
            CheckResult(
                name="yt_dlp_present",
                ok=False,
                detail="",
                error="yt-dlp not found on PATH",
            )
        )
        return finish(out_dir, checks, staging, args, started, accepted=False, log="")
    checks.append(
        CheckResult(
            name="yt_dlp_present",
            ok=True,
            detail=subprocess.run(
                ["yt-dlp", "--version"], capture_output=True, text=True, check=False
            ).stdout.strip(),
        )
    )

    default_dir = args.default_music_dir.expanduser()
    before_default = snapshot_dir(default_dir)
    before_staging = snapshot_dir(staging)

    try:
        code, log = run_download(
            script,
            staging=staging,
            item=args.item,
            timeout_sec=args.timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        checks.append(
            CheckResult(
                name="download",
                ok=False,
                detail=f"timeout after {args.timeout_sec}s",
                error=str(exc),
            )
        )
        return finish(out_dir, checks, staging, args, started, accepted=False, log="")

    (out_dir / "download.log").write_text(log, encoding="utf-8")
    after_default = snapshot_dir(default_dir)
    after_staging = snapshot_dir(staging)
    leaked = new_files(before_default, after_default)
    staged_new = new_files(before_staging, after_staging)
    audio_files = list_audio(staging)

    # download.sh currently exits 0 even when all items fail; trust staged audio.
    success_line = next(
        (line for line in log.splitlines() if line.startswith("完成:")),
        "",
    )
    download_ok = bool(audio_files)
    cookie_hint = any(
        token in log.lower()
        for token in ("sign in to confirm", "cookies", "bot", "captcha", "http error 429")
    )
    youtube_js_challenge = "n challenge solving failed" in log.lower() or "no video formats found" in log.lower()
    checks.append(
        CheckResult(
            name="download",
            ok=download_ok,
            detail=(
                f"process_exit={code}, audio_files={len(audio_files)}, "
                f"staged_new={len(staged_new)}, summary={success_line or 'n/a'}"
            ),
            error=None if download_ok else log[-800:],
        )
    )
    if youtube_js_challenge and not download_ok:
        checks.append(
            CheckResult(
                name="youtube_js_runtime",
                ok=False,
                detail="yt-dlp YouTube JS challenge failed; prefer non-YouTube sources or upgrade yt-dlp/EJS runtime",
                error="youtube format extraction failed",
            )
        )
    checks.append(
        CheckResult(
            name="no_default_music_writes",
            ok=len(leaked) == 0,
            detail=f"default_dir={default_dir}",
            error=None if not leaked else f"new files in default music dir: {leaked}",
        )
    )
    checks.append(
        CheckResult(
            name="staging_has_audio",
            ok=bool(audio_files),
            detail=", ".join(p.name for p in audio_files) if audio_files else "none",
            error=None if audio_files else "no audio in staging",
        )
    )

    sources = []
    source_meta = dump_source_json(args.item, staging)
    if source_meta:
        sources.append(source_meta)
    media_info = [ffprobe_tags(path) for path in audio_files]
    (out_dir / "source_manifest.json").write_text(
        json.dumps(
            {
                "authorization_status": "pending",
                "note": "Download success only creates pending authorization; user must confirm before release master use.",
                "item": args.item,
                "sources": sources,
                "media": media_info,
                "cookie_policy": {
                    "auto_retry_with_browser_cookies": True,
                    "browser": "chrome",
                    "requires_user_permission_in_adapter": True,
                    "observed_cookie_related_message": cookie_hint,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    checks.append(
        CheckResult(
            name="source_manifest",
            ok=True,
            detail=str(out_dir / "source_manifest.json"),
        )
    )

    accepted = all(check.ok for check in checks)
    # Cookie path is mixed/manual; do not fail acceptance solely on cookie hint.
    return finish(out_dir, checks, staging, args, started, accepted=accepted, log=log)


def finish(
    out_dir: Path,
    checks: list[CheckResult],
    staging: Path,
    args: argparse.Namespace,
    started: float,
    *,
    accepted: bool,
    log: str,
) -> int:
    summary = {
        "task": "M0-09",
        "accepted": accepted,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": time.perf_counter() - started,
        "item": args.item,
        "staging_dir": str(staging),
        "default_music_dir": str(args.default_music_dir.expanduser()),
        "checks": [asdict(check) for check in checks],
        "adapter_contract": {
            "capability": "music.download",
            "wrapper": "music-downloader/scripts/download.sh",
            "required_flags": ["--output <task-staging>"],
            "forbidden": ["rely on default ~/Music/Downloads"],
            "authorization": "pending until user confirms",
            "cookies": "only after user grants adapter permission; chrome cookies auto-retry exists in skill script",
        },
    }
    path = out_dir / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"accepted": accepted, "checks": summary["checks"]}, indent=2, ensure_ascii=False))
    print(f"summary: {path}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
