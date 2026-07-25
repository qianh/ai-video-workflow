"""SQLite connection helpers with WAL and foreign keys."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Database:
    path: Path
    connection: sqlite3.Connection

    def close(self) -> None:
        self.connection.close()

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def executescript(self, sql: str) -> None:
        self.connection.executescript(sql)

    def commit(self) -> None:
        self.connection.commit()

    def fetchone(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        cursor = self.connection.execute(sql, params)
        return cursor.fetchone()

    def fetchall(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        cursor = self.connection.execute(sql, params)
        return list(cursor.fetchall())


def open_database(path: Path) -> Database:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return Database(path=path, connection=connection)
