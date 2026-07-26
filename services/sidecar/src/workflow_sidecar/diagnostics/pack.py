"""Diagnostic pack builder — no secrets, no .env.local, no media originals."""

from __future__ import annotations

import json
import platform
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..persistence.timeutil import utc_now
from .logging import JsonlLogger, redact_text


@dataclass(frozen=True)
class DiagnosticPackInfo:
    path: str
    created_at: str
    size_bytes: int
    includes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_diagnostic_pack(
    *,
    output_dir: Path,
    app_version: str = "0.1.0",
    global_db_path: Path | None = None,
    project_root: Path | None = None,
    project_schema_version: int | None = None,
    job_summary: list[dict[str, Any]] | None = None,
    capability_status: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> DiagnosticPackInfo:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().replace(":", "").replace(".", "")
    pack_path = output_dir / f"diagnostic-{stamp}.zip"
    includes: list[str] = []
    staging = output_dir / f".diag-staging-{stamp}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    meta = {
        "created_at": utc_now(),
        "app_version": app_version,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "project_schema_version": project_schema_version,
        "has_project": project_root is not None,
        "global_db_present": bool(global_db_path and global_db_path.is_file()),
        "note": "Secrets, .env.local, media originals and full prompts are excluded.",
    }
    (staging / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    includes.append("meta.json")

    if job_summary is not None:
        (staging / "jobs.json").write_text(
            json.dumps(job_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        includes.append("jobs.json")

    if capability_status is not None:
        (staging / "capabilities.json").write_text(
            json.dumps(capability_status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        includes.append("capabilities.json")

    if log_path is not None and log_path.is_file():
        # Re-redact lines defensively.
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        redacted_lines = [redact_text(line) for line in raw.splitlines()[-500:]]
        (staging / "logs-tail.jsonl").write_text(
            "\n".join(redacted_lines) + ("\n" if redacted_lines else ""),
            encoding="utf-8",
        )
        includes.append("logs-tail.jsonl")

    if project_root is not None:
        project_json = project_root / "project.json"
        if project_json.is_file():
            # project.json should not contain secrets; copy as-is after text redact.
            text = redact_text(project_json.read_text(encoding="utf-8", errors="replace"))
            (staging / "project.json").write_text(text, encoding="utf-8")
            includes.append("project.json")
        # Explicitly never include .env.local
        assert not (staging / ".env.local").exists()

    with zipfile.ZipFile(pack_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for child in staging.iterdir():
            if child.is_file():
                archive.write(child, arcname=child.name)

    shutil.rmtree(staging, ignore_errors=True)
    return DiagnosticPackInfo(
        path=str(pack_path),
        created_at=meta["created_at"],
        size_bytes=pack_path.stat().st_size,
        includes=includes,
    )


def default_log_path(*, project_root: Path | None, global_db_path: Path) -> Path:
    if project_root is not None:
        return project_root / "logs" / "sidecar.jsonl"
    return global_db_path.parent / "logs" / "app.jsonl"
