"""SQLite persistence — Python sidecar is the sole writer (ADR-003)."""

from .database import Database, open_database
from .migrations import GLOBAL_MIGRATIONS, PROJECT_MIGRATIONS, apply_migrations
from .workspace import ProjectRecord, WorkspaceService

__all__ = [
    "Database",
    "GLOBAL_MIGRATIONS",
    "PROJECT_MIGRATIONS",
    "ProjectRecord",
    "WorkspaceService",
    "apply_migrations",
    "open_database",
]
