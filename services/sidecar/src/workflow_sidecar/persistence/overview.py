"""Project overview aggregation for M1-10 shell."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .jobs import JobQueue
from .snapshots import list_snapshots
from .workspace import ProjectRecord


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def build_project_overview(
    *,
    project: ProjectRecord,
    jobs: JobQueue,
) -> dict[str, Any]:
    root = Path(project.root_path)
    all_jobs = jobs.list(limit=200)
    counts: dict[str, int] = {
        "queued": 0,
        "running": 0,
        "paused": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for job in all_jobs:
        counts[job.status] = counts.get(job.status, 0) + 1

    failed_jobs = [
        {
            "id": job.id,
            "kind": job.kind,
            "status": job.status,
            "attempts": job.attempts,
            "last_error": job.last_error,
        }
        for job in all_jobs
        if job.status == "failed"
    ][:10]

    disk = {
        "assets_bytes": _dir_size_bytes(root / "assets"),
        "renders_bytes": _dir_size_bytes(root / "renders"),
        "temp_bytes": _dir_size_bytes(root / "temp"),
        "logs_bytes": _dir_size_bytes(root / "logs"),
        "snapshots_bytes": _dir_size_bytes(root / "snapshots"),
        "project_db_bytes": _dir_size_bytes(root / "project.db"),
    }
    disk["total_bytes"] = sum(disk.values())

    snapshots = [item.as_dict() for item in list_snapshots(root)[:8]]

    return {
        "project": project.as_dict(),
        "job_counts": counts,
        "failed_jobs": failed_jobs,
        "disk": disk,
        "snapshots": snapshots,
        "queue_depth": counts["queued"] + counts["running"] + counts["paused"],
    }
