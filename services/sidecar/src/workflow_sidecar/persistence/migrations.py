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
    ),
    (
        2,
        """
        CREATE TABLE story_sources (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            text_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE source_chunks (
            id TEXT PRIMARY KEY,
            story_source_id TEXT NOT NULL,
            parent_chunk_id TEXT,
            chunk_type TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            title TEXT,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            split_batch_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(story_source_id) REFERENCES story_sources(id)
        );

        CREATE INDEX idx_source_chunks_source ON source_chunks(story_source_id, ordinal);

        CREATE TABLE story_branches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_branch_id TEXT,
            is_primary INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE narrative_events (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            stable_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(branch_id) REFERENCES story_branches(id),
            UNIQUE(branch_id, stable_key)
        );

        CREATE TABLE narrative_event_revisions (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            order_key REAL NOT NULL,
            story_time TEXT,
            origin TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL,
            story_source_id TEXT,
            source_chunk_id TEXT,
            char_start INTEGER,
            char_end INTEGER,
            quote_hash TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(event_id) REFERENCES narrative_events(id),
            FOREIGN KEY(branch_id) REFERENCES story_branches(id)
        );

        CREATE INDEX idx_event_revisions_branch ON narrative_event_revisions(branch_id, order_key);

        CREATE TABLE narrative_event_edges (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            from_event_id TEXT NOT NULL,
            to_event_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(branch_id) REFERENCES story_branches(id),
            FOREIGN KEY(from_event_id) REFERENCES narrative_events(id),
            FOREIGN KEY(to_event_id) REFERENCES narrative_events(id)
        );

        CREATE INDEX idx_event_edges_branch ON narrative_event_edges(branch_id);
        """,
    ),
    (
        3,
        """
        CREATE TABLE creative_packs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            pack_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE creative_pack_revisions (
            id TEXT PRIMARY KEY,
            pack_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            rules_json TEXT NOT NULL,
            resources_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(pack_id) REFERENCES creative_packs(id),
            UNIQUE(pack_id, version)
        );

        CREATE TABLE creative_pack_compositions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE creative_pack_composition_revisions (
            id TEXT PRIMARY KEY,
            composition_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            visual_revision_id TEXT NOT NULL,
            narrative_revision_id TEXT NOT NULL,
            technique_revision_ids_json TEXT NOT NULL,
            resolution_order_json TEXT NOT NULL,
            resolved_rules_json TEXT NOT NULL,
            resource_hashes_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(composition_id) REFERENCES creative_pack_compositions(id),
            UNIQUE(composition_id, version)
        );

        CREATE TABLE project_creative_pack_locks (
            id TEXT PRIMARY KEY,
            composition_revision_id TEXT NOT NULL,
            composition_content_hash TEXT NOT NULL,
            purpose TEXT NOT NULL,
            locked_at TEXT NOT NULL,
            FOREIGN KEY(composition_revision_id) REFERENCES creative_pack_composition_revisions(id)
        );

        CREATE TABLE creative_pack_evaluations (
            id TEXT PRIMARY KEY,
            composition_revision_id TEXT NOT NULL,
            suite_id TEXT NOT NULL,
            result TEXT NOT NULL,
            structural_ok INTEGER NOT NULL,
            rules_ok INTEGER NOT NULL,
            notes_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(composition_revision_id) REFERENCES creative_pack_composition_revisions(id)
        );

        CREATE INDEX idx_pack_revisions_pack ON creative_pack_revisions(pack_id, version);
        CREATE INDEX idx_composition_revisions ON creative_pack_composition_revisions(composition_id, version);
        CREATE INDEX idx_pack_locks ON project_creative_pack_locks(locked_at DESC);
        """,
    ),
    (
        4,
        """
        ALTER TABLE story_branches ADD COLUMN forked_from_revision_id TEXT;
        """,
    ),
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
