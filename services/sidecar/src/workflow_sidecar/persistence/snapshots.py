"""Lightweight project database snapshots (M1-11)."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .timeutil import utc_now


@dataclass(frozen=True)
class SnapshotInfo:
    name: str
    path: str
    created_at: str
    size_bytes: int
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def snapshots_dir(project_root: Path) -> Path:
    path = project_root / "snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_db_snapshot(
    project_root: Path,
    *,
    reason: str = "manual",
    source_name: str = "project.db",
) -> SnapshotInfo:
    project_root = project_root.resolve()
    source = project_root / source_name
    if not source.is_file():
        raise ValueError(f"{source_name} not found under {project_root}")

    stamp = utc_now().replace(":", "").replace(".", "")
    safe_reason = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in reason)[:40]
    name = f"{source_name}.{stamp}.{safe_reason or 'snapshot'}.bak"
    target = snapshots_dir(project_root) / name
    # Copy main db; ignore -wal/-shm for a consistent "best effort" light snapshot.
    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        side = Path(str(source) + suffix)
        if side.is_file():
            shutil.copy2(side, Path(str(target) + suffix))

    stat = target.stat()
    return SnapshotInfo(
        name=name,
        path=str(target.relative_to(project_root)),
        created_at=utc_now(),
        size_bytes=stat.st_size,
        reason=reason,
    )


def list_snapshots(project_root: Path) -> list[SnapshotInfo]:
    root = project_root.resolve()
    directory = snapshots_dir(root)
    items: list[SnapshotInfo] = []
    for path in sorted(directory.glob("*.bak"), reverse=True):
        if path.name.endswith(("-wal", "-shm")):
            continue
        # recover reason from name if present
        parts = path.name.split(".")
        reason = parts[-2] if len(parts) >= 3 else "unknown"
        items.append(
            SnapshotInfo(
                name=path.name,
                path=str(path.relative_to(root)),
                created_at="",
                size_bytes=path.stat().st_size,
                reason=reason,
            )
        )
    return items
