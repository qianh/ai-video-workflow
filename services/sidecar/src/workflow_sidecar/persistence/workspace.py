"""Project create/open/close and recent list (ADR-003, ADR-005)."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .database import Database, open_database
from .migrations import (
    GLOBAL_MIGRATIONS,
    PROJECT_MIGRATIONS,
    apply_migrations,
    current_version,
)
from .snapshots import create_db_snapshot
from .timeutil import utc_now

PROJECT_DIRS = (
    "sources/normalized",
    "creative-packs",
    "assets/images",
    "assets/videos",
    "assets/audio",
    "assets/subtitles",
    "assets/documents",
    "proxies",
    "renders/previews",
    "renders/masters",
    "renders/platforms",
    "snapshots",
    "temp",
    "logs",
    "manifests",
)


def slugify(name: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "project"


@dataclass
class ProjectRecord:
    id: str
    name: str
    root_path: str
    schema_version: int
    opened_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class WorkspaceService:
    """Owns global.db and at most one open project.db connection."""

    def __init__(self, global_db_path: Path) -> None:
        self._global_db_path = Path(global_db_path)
        self._global = open_database(self._global_db_path)
        apply_migrations(self._global, GLOBAL_MIGRATIONS)
        self._project: Database | None = None
        self._current: ProjectRecord | None = None

    @property
    def global_db_path(self) -> Path:
        return self._global_db_path

    @property
    def current(self) -> ProjectRecord | None:
        return self._current

    def require_project_db(self) -> Database:
        if self._project is None or self._current is None:
            raise ValueError("no project is open")
        return self._project

    def close(self) -> None:
        self.close_project()
        self._global.close()

    def close_project(self) -> ProjectRecord | None:
        previous = self._current
        if self._project is not None:
            self._project.close()
            self._project = None
        self._current = None
        return previous

    def list_recent(self, limit: int = 20) -> list[ProjectRecord]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self._global.fetchall(
            """
            SELECT id, name, root_path, opened_at
            FROM recent_projects
            ORDER BY opened_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            ProjectRecord(
                id=row["id"],
                name=row["name"],
                root_path=row["root_path"],
                schema_version=0,
                opened_at=row["opened_at"],
            )
            for row in rows
        ]

    def create_project(self, parent_dir: str | Path, name: str) -> ProjectRecord:
        parent = Path(parent_dir).expanduser().resolve()
        if not parent.is_dir():
            raise ValueError(f"parent_dir does not exist: {parent}")
        safe_name = name.strip()
        if not safe_name:
            raise ValueError("name must be a non-empty string")

        root = parent / slugify(safe_name)
        if root.exists():
            raise ValueError(f"project path already exists: {root}")

        root.mkdir(parents=True, exist_ok=False)
        for relative in PROJECT_DIRS:
            (root / relative).mkdir(parents=True, exist_ok=True)

        project_id = str(uuid.uuid4())
        created_at = utc_now()
        project_json = {
            "id": project_id,
            "name": safe_name,
            "created_at": created_at,
            "schema_version": 1,
        }
        (root / "project.json").write_text(
            json.dumps(project_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        db_path = root / "project.db"
        project_db = open_database(db_path)
        try:
            version = apply_migrations(project_db, PROJECT_MIGRATIONS)
            project_db.execute(
                "INSERT INTO project_meta(key, value_json) VALUES (?, ?)",
                ("identity", json.dumps(project_json, ensure_ascii=False)),
            )
            project_db.commit()
        finally:
            project_db.close()

        self._remember(project_id, safe_name, root, created_at)
        return self.open_project(root)

    def open_project(self, root_dir: str | Path) -> ProjectRecord:
        root = Path(root_dir).expanduser().resolve()
        db_path = root / "project.db"
        if not db_path.is_file():
            raise ValueError(f"project.db not found under {root}")

        self.close_project()
        project_db = open_database(db_path)
        try:
            before = current_version(project_db)
            target = max((version for version, _ in PROJECT_MIGRATIONS), default=0)
            if before < target:
                create_db_snapshot(
                    root,
                    reason=f"pre-migration-v{before}-to-v{target}",
                )
            version = apply_migrations(project_db, PROJECT_MIGRATIONS)
            identity = project_db.fetchone(
                "SELECT value_json FROM project_meta WHERE key = 'identity'"
            )
            if identity is None:
                raise ValueError("project identity meta missing")
            payload = json.loads(identity["value_json"])
            project_id = str(payload["id"])
            name = str(payload["name"])
            opened_at = utc_now()
            record = ProjectRecord(
                id=project_id,
                name=name,
                root_path=str(root),
                schema_version=version,
                opened_at=opened_at,
            )
            self._project = project_db
            self._current = record
            self._remember(project_id, name, root, opened_at)
            return record
        except Exception:
            project_db.close()
            raise

    def _remember(self, project_id: str, name: str, root: Path, opened_at: str) -> None:
        existing = self._global.fetchone(
            "SELECT created_at FROM recent_projects WHERE id = ?",
            (project_id,),
        )
        created_at = existing["created_at"] if existing is not None else opened_at
        self._global.connection.execute("BEGIN IMMEDIATE")
        try:
            self._global.execute(
                """
                INSERT INTO recent_projects(id, name, root_path, opened_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    root_path = excluded.root_path,
                    opened_at = excluded.opened_at
                """,
                (project_id, name, str(root), opened_at, created_at),
            )
            self._global.connection.execute("COMMIT")
        except Exception:
            self._global.connection.execute("ROLLBACK")
            raise
