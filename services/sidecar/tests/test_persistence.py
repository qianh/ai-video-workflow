from __future__ import annotations

from pathlib import Path

import pytest

from workflow_sidecar.persistence import (
    GLOBAL_MIGRATIONS,
    PROJECT_MIGRATIONS,
    WorkspaceService,
    apply_migrations,
    open_database,
)


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    db = open_database(tmp_path / "t.db")
    v1 = apply_migrations(db, GLOBAL_MIGRATIONS)
    v2 = apply_migrations(db, GLOBAL_MIGRATIONS)
    assert v1 == 1
    assert v2 == 1
    db.close()


def test_create_open_close_project_uses_relative_layout(tmp_path: Path) -> None:
    global_db = tmp_path / "global.db"
    parent = tmp_path / "projects"
    parent.mkdir()
    service = WorkspaceService(global_db)

    created = service.create_project(parent, "夜市试播")
    assert created.name == "夜市试播"
    root = Path(created.root_path)
    assert (root / "project.db").is_file()
    assert (root / "project.json").is_file()
    assert (root / "assets" / "images").is_dir()
    assert (root / "temp").is_dir()
    assert service.current is not None
    assert service.current.id == created.id

    service.close_project()
    assert service.current is None

    reopened = service.open_project(root)
    assert reopened.id == created.id
    assert reopened.schema_version >= 13

    recent = service.list_recent()
    assert len(recent) == 1
    assert recent[0].root_path == str(root.resolve())

    # project.db must not store absolute asset paths in v1 identity meta
    row = service._project.fetchone(  # noqa: SLF001 - test inspects writer internals
        "SELECT value_json FROM project_meta WHERE key = 'identity'"
    )
    assert row is not None
    assert "/assets/" not in row["value_json"]

    service.close()


def test_create_rejects_existing_directory(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "p"
    parent.mkdir()
    service.create_project(parent, "demo")
    with pytest.raises(ValueError, match="already exists"):
        service.create_project(parent, "demo")
    service.close()


def test_project_migrations_create_jobs_table(tmp_path: Path) -> None:
    db = open_database(tmp_path / "project.db")
    apply_migrations(db, PROJECT_MIGRATIONS)
    row = db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
    )
    assert row is not None
    db.close()
