"""SQLite persistence — Python sidecar is the sole writer (ADR-003)."""

from .database import Database, open_database
from .envconfig import default_global_env_path, resolve_task_env, summarize_env
from .jobs import JobQueue, JobRecord
from .migrations import GLOBAL_MIGRATIONS, PROJECT_MIGRATIONS, apply_migrations
from .paths import file_sha256, resolve_project_path, to_project_relative
from .snapshots import SnapshotInfo, create_db_snapshot, list_snapshots
from .workspace import ProjectRecord, WorkspaceService

__all__ = [
    "Database",
    "GLOBAL_MIGRATIONS",
    "JobQueue",
    "JobRecord",
    "PROJECT_MIGRATIONS",
    "ProjectRecord",
    "SnapshotInfo",
    "WorkspaceService",
    "apply_migrations",
    "create_db_snapshot",
    "default_global_env_path",
    "file_sha256",
    "list_snapshots",
    "open_database",
    "resolve_project_path",
    "resolve_task_env",
    "summarize_env",
    "to_project_relative",
]
