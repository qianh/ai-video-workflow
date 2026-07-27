"""Assets, files, links, sources and licenses (M3-08)."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .database import Database
from .timeutil import utc_now

ASSET_TYPES = frozenset(
    {"image", "video", "audio", "subtitle", "document", "font", "other"}
)
ASSET_STATUSES = frozenset(
    {"draft", "candidate", "selected", "approved", "archived", "rejected"}
)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AssetService:
    def __init__(self, db: Database, project_root: Path) -> None:
        self._db = db
        self._root = Path(project_root)

    def create_asset(
        self,
        *,
        title: str,
        asset_type: str,
        role: str = "generic",
        relative_path: str | None = None,
        bytes_data: bytes | None = None,
        mime_type: str | None = None,
        license_status: str = "pending",
    ) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("title is required")
        if asset_type not in ASSET_TYPES:
            raise ValueError(f"asset_type must be one of {sorted(ASSET_TYPES)}")
        asset_id = str(uuid.uuid4())
        file_id = None
        now = utc_now()
        content_hash = None
        byte_size = 0
        rel = relative_path
        if bytes_data is not None:
            content_hash = _hash_bytes(bytes_data)
            byte_size = len(bytes_data)
            if not rel:
                rel = f"assets/{asset_type}s/{asset_id[:8]}_{title[:20].replace(' ', '_')}.bin"
            dest = self._root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(bytes_data)
        elif rel:
            path = self._root / rel
            if path.is_file():
                data = path.read_bytes()
                content_hash = _hash_bytes(data)
                byte_size = len(data)
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO assets(
                    id, project_scoped, asset_type, role, title, status,
                    selected_file_id, license_status, locked, content_fingerprint,
                    created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, 'candidate', NULL, ?, 0, ?, ?, ?)
                """,
                (
                    asset_id,
                    asset_type,
                    role,
                    title,
                    license_status,
                    content_hash,
                    now,
                    now,
                ),
            )
            if rel and content_hash is not None:
                file_id = str(uuid.uuid4())
                self._db.execute(
                    """
                    INSERT INTO asset_files(
                        id, asset_id, relative_path, content_hash, byte_size,
                        mime_type, width, height, duration_ms, is_proxy,
                        availability, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 'online', ?)
                    """,
                    (
                        file_id,
                        asset_id,
                        rel,
                        content_hash,
                        byte_size,
                        mime_type,
                        now,
                    ),
                )
                self._db.execute(
                    """
                    UPDATE assets SET selected_file_id = ?, status = 'selected'
                    WHERE id = ?
                    """,
                    (file_id, asset_id),
                )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_asset(asset_id)

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        row = self._db.fetchone("SELECT * FROM assets WHERE id = ?", (asset_id,))
        if row is None:
            raise ValueError(f"asset not found: {asset_id}")
        files = self._db.fetchall(
            "SELECT * FROM asset_files WHERE asset_id = ? ORDER BY created_at",
            (asset_id,),
        )
        return {
            "id": row["id"],
            "asset_type": row["asset_type"],
            "role": row["role"],
            "title": row["title"],
            "status": row["status"],
            "selected_file_id": row["selected_file_id"],
            "license_status": row["license_status"],
            "locked": bool(row["locked"]),
            "content_fingerprint": row["content_fingerprint"],
            "files": [
                {
                    "id": f["id"],
                    "relative_path": f["relative_path"],
                    "content_hash": f["content_hash"],
                    "byte_size": int(f["byte_size"]),
                    "mime_type": f["mime_type"],
                    "availability": f["availability"],
                }
                for f in files
            ],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_assets(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self._db.fetchall(
            "SELECT id FROM assets ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [self.get_asset(row["id"]) for row in rows]

    def link_asset(
        self,
        *,
        owner_type: str,
        owner_id: str,
        asset_id: str,
        usage_role: str,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        self.get_asset(asset_id)
        link_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO asset_links(
                id, owner_type, owner_id, asset_id, usage_role, sort_order, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (link_id, owner_type, owner_id, asset_id, usage_role, sort_order, now),
        )
        self._db.commit()
        return {
            "id": link_id,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "asset_id": asset_id,
            "usage_role": usage_role,
            "sort_order": sort_order,
        }

    def lock_asset(self, asset_id: str, locked: bool = True) -> dict[str, Any]:
        self.get_asset(asset_id)
        self._db.execute(
            "UPDATE assets SET locked = ?, updated_at = ? WHERE id = ?",
            (1 if locked else 0, utc_now(), asset_id),
        )
        self._db.commit()
        return self.get_asset(asset_id)

    def confirm_license(
        self,
        asset_id: str,
        *,
        license_type: str | None = None,
        usage_scope: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        self.get_asset(asset_id)
        now = utc_now()
        rec_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO license_records(
                id, asset_id, status, license_type, usage_scope,
                confirmation_note, created_at
            ) VALUES (?, ?, 'confirmed_by_user', ?, ?, ?, ?)
            """,
            (rec_id, asset_id, license_type, usage_scope, note, now),
        )
        self._db.execute(
            """
            UPDATE assets SET license_status = 'confirmed_by_user', updated_at = ?
            WHERE id = ?
            """,
            (now, asset_id),
        )
        self._db.commit()
        return {"license_record_id": rec_id, "asset": self.get_asset(asset_id)}

    def add_source_record(
        self,
        *,
        asset_id: str | None,
        url: str | None,
        platform: str | None = None,
        title: str | None = None,
        author: str | None = None,
        tool: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rec_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO source_records(
                id, asset_id, url, platform, title, author, tool, fetched_at, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec_id,
                asset_id,
                url,
                platform,
                title,
                author,
                tool,
                now,
                _stable_json(meta or {}),
            ),
        )
        self._db.commit()
        return {
            "id": rec_id,
            "asset_id": asset_id,
            "url": url,
            "platform": platform,
            "title": title,
            "tool": tool,
            "fetched_at": now,
        }

    def find_duplicates(self, content_hash: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT a.id AS asset_id, f.id AS file_id, f.relative_path, f.byte_size
            FROM asset_files f
            JOIN assets a ON a.id = f.asset_id
            WHERE f.content_hash = ?
            """,
            (content_hash,),
        )
        return [dict(row) for row in rows]
