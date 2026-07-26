from __future__ import annotations

from pathlib import Path

import pytest

from workflow_sidecar.persistence.paths import (
    ensure_under_project,
    file_sha256,
    resolve_project_path,
    to_project_relative,
)


def test_resolve_rejects_escape_and_absolute(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "assets").mkdir()
    (root / "assets" / "a.txt").write_text("hi", encoding="utf-8")

    resolved = resolve_project_path(root, "assets/a.txt")
    assert resolved == (root / "assets" / "a.txt").resolve()
    assert to_project_relative(root, resolved) == "assets/a.txt"

    with pytest.raises(ValueError, match="absolute"):
        resolve_project_path(root, "/etc/passwd")
    with pytest.raises(ValueError, match="escapes"):
        resolve_project_path(root, "../outside.txt")
    with pytest.raises(ValueError, match="outside"):
        to_project_relative(root, tmp_path / "other.txt")


def test_file_sha256_and_ensure(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    target = root / "assets" / "x.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"abc")
    digest = file_sha256(target)
    assert len(digest) == 64
    assert ensure_under_project(root, target) == target.resolve()
    with pytest.raises(ValueError, match="escapes"):
        ensure_under_project(root, tmp_path / "nope")
