"""Incremental SQLite migrations for global.db and project.db."""

from __future__ import annotations

from .database import Database

Migration = tuple[int, str]

GLOBAL_MIGRATIONS: list[Migration] = [
    (
        1,
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE recent_projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            root_path TEXT NOT NULL UNIQUE,
            opened_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );

        CREATE TABLE component_status (
            code TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            version TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        """,
    )
]

PROJECT_MIGRATIONS: list[Migration] = [
    (
        1,
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE project_meta (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );

        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            lease_owner TEXT,
            lease_until TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_jobs_status ON jobs(status);
        """,
    )
]


def current_version(db: Database) -> int:
    row = db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if row is None:
        return 0
    version_row = db.fetchone(
        "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
    )
    return int(version_row["v"]) if version_row is not None else 0


def apply_migrations(db: Database, migrations: list[Migration]) -> int:
    """Apply pending migrations. sqlite3.executescript auto-commits, so each
    version is applied as its own script plus a version insert.
    """

    applied = current_version(db)
    target = applied
    for version, sql in migrations:
        if version <= applied:
            continue
        # Keep version insert inside the same script body to avoid partial apply.
        bundled = (
            sql
            + f"\nINSERT INTO schema_migrations(version) VALUES ({int(version)});\n"
        )
        db.executescript(bundled)
        target = version
    return target
