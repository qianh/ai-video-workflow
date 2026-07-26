"""Project-relative path safety and file integrity helpers (M1-06)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def resolve_project_path(project_root: Path, relative: str) -> Path:
    """Resolve a project-relative path; reject escape and absolute inputs."""

    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("relative path must be a non-empty string")
    candidate = relative.strip()
    if candidate.startswith(("/", "\\")) or (len(candidate) > 1 and candidate[1] == ":"):
        raise ValueError("absolute paths are not allowed")
    if "\x00" in candidate:
        raise ValueError("invalid path")

    root = project_root.resolve()
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes project root") from exc
    return target


def to_project_relative(project_root: Path, path: Path) -> str:
    root = project_root.resolve()
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root))
    except ValueError as exc:
        raise ValueError("path is outside project root") from exc


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def ensure_under_project(project_root: Path, path: Path) -> Path:
    root = project_root.resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes project root") from exc
    return resolved
