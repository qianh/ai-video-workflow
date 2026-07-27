"""Structured drafts, schema validation, and formal revisions (M2-05).

Formal revisions can only be created by promoting a validated draft.
There is no API path that writes formal content without that gate.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import Database
from .timeutil import utc_now

# Minimal built-in schemas for M2 structured content.
BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "episode_outline_v1": {
        "type": "object",
        "required": ["episode_no", "title", "summary", "hooks"],
        "properties": {
            "episode_no": {"type": "integer", "minimum": 1},
            "title": {"type": "string", "minLength": 1},
            "summary": {"type": "string", "minLength": 1},
            "hooks": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "scenes": {"type": "array"},
        },
        "additionalProperties": True,
    },
    "character_bible_v1": {
        "type": "object",
        "required": ["name", "role", "traits"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "role": {"type": "string", "minLength": 1},
            "traits": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "voice": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "scene_bible_v1": {
        "type": "object",
        "required": ["name", "location_type", "time_of_day"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "location_type": {"type": "string", "minLength": 1},
            "time_of_day": {"type": "string", "minLength": 1},
            "props": {"type": "array"},
        },
        "additionalProperties": True,
    },
}

DRAFT_STATUSES = frozenset({"draft", "validated", "rejected", "promoted"})
TARGET_TYPES = frozenset(
    {"episode_outline", "character_bible", "scene_bible", "generic"}
)


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


def validate_against_schema(
    payload: Any, schema: dict[str, Any]
) -> list[str]:
    """Tiny JSON-schema subset validator (object/required/types/minLength/minItems)."""

    errors: list[str] = []

    def walk(value: Any, node: dict[str, Any], path: str) -> None:
        expected = node.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                errors.append(f"{path or '$'}: expected object")
                return
            for key in node.get("required", []):
                if key not in value:
                    errors.append(f"{path}.{key}: required".lstrip("."))
            props = node.get("properties", {})
            for key, child in value.items():
                if key in props:
                    walk(child, props[key], f"{path}.{key}" if path else key)
                elif node.get("additionalProperties") is False:
                    errors.append(f"{path}.{key}: additional property not allowed")
        elif expected == "array":
            if not isinstance(value, list):
                errors.append(f"{path}: expected array")
                return
            min_items = node.get("minItems")
            if isinstance(min_items, int) and len(value) < min_items:
                errors.append(f"{path}: minItems {min_items}")
            item_schema = node.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    walk(item, item_schema, f"{path}[{index}]")
        elif expected == "string":
            if not isinstance(value, str):
                errors.append(f"{path}: expected string")
                return
            min_len = node.get("minLength")
            if isinstance(min_len, int) and len(value) < min_len:
                errors.append(f"{path}: minLength {min_len}")
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{path}: expected integer")
                return
            minimum = node.get("minimum")
            if isinstance(minimum, int) and value < minimum:
                errors.append(f"{path}: minimum {minimum}")
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{path}: expected number")

    walk(payload, schema, "")
    return errors


class DraftService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        *,
        schema_id: str,
        title: str,
        payload: dict[str, Any],
        target_type: str = "generic",
        target_id: str | None = None,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        if schema_id not in BUILTIN_SCHEMAS:
            raise ValueError(f"unknown schema_id: {schema_id}")
        if target_type not in TARGET_TYPES:
            raise ValueError(f"invalid target_type: {target_type}")
        safe_title = title.strip()
        if not safe_title:
            raise ValueError("title must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        draft_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO content_drafts(
                id, target_type, target_id, branch_id, schema_id, title,
                payload_json, status, validation_errors_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', '[]', ?, ?)
            """,
            (
                draft_id,
                target_type,
                target_id,
                branch_id,
                schema_id,
                safe_title,
                _stable_json(payload),
                now,
                now,
            ),
        )
        self._db.commit()
        return self.get(draft_id)

    def update(
        self,
        draft_id: str,
        *,
        payload: dict[str, Any] | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        draft = self.get(draft_id)
        if draft["status"] == "promoted":
            raise ValueError("promoted draft is immutable")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        next_title = draft["title"] if title is None else title.strip()
        if not next_title:
            raise ValueError("title must be a non-empty string")
        next_payload = draft["payload"] if payload is None else payload
        now = utc_now()
        # Any edit resets validation gate.
        self._db.execute(
            """
            UPDATE content_drafts
            SET title = ?, payload_json = ?, status = 'draft',
                validation_errors_json = '[]', updated_at = ?
            WHERE id = ?
            """,
            (next_title, _stable_json(next_payload), now, draft_id),
        )
        self._db.commit()
        return self.get(draft_id)

    def validate(self, draft_id: str) -> dict[str, Any]:
        draft = self.get(draft_id)
        if draft["status"] == "promoted":
            raise ValueError("promoted draft cannot be revalidated")
        schema = BUILTIN_SCHEMAS[draft["schema_id"]]
        errors = validate_against_schema(draft["payload"], schema)
        status = "validated" if not errors else "rejected"
        now = utc_now()
        self._db.execute(
            """
            UPDATE content_drafts
            SET status = ?, validation_errors_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, _stable_json(errors), now, draft_id),
        )
        self._db.commit()
        result = self.get(draft_id)
        result["validation_errors"] = errors
        return result

    def promote(
        self,
        draft_id: str,
        *,
        require_primary_branch: bool = True,
        primary_branch_id: str | None = None,
    ) -> dict[str, Any]:
        draft = self.get(draft_id)
        if draft["status"] != "validated":
            raise ValueError(
                "only validated drafts can become formal revisions; run draft.validate first"
            )
        if require_primary_branch and draft["branch_id"] and primary_branch_id:
            if draft["branch_id"] != primary_branch_id:
                raise ValueError(
                    "only drafts on the primary production branch can be promoted"
                )

        # Re-validate at promote time to prevent stale validated status.
        schema = BUILTIN_SCHEMAS[draft["schema_id"]]
        errors = validate_against_schema(draft["payload"], schema)
        if errors:
            now = utc_now()
            self._db.execute(
                """
                UPDATE content_drafts
                SET status = 'rejected', validation_errors_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (_stable_json(errors), now, draft_id),
            )
            self._db.commit()
            raise ValueError(f"schema validation failed: {errors[0]}")

        target_key = draft["target_id"] or draft_id
        row = self._db.fetchone(
            """
            SELECT COALESCE(MAX(revision_no), 0) AS v
            FROM formal_revisions
            WHERE target_type = ? AND target_id = ?
            """,
            (draft["target_type"], target_key),
        )
        revision_no = int(row["v"]) + 1 if row else 1
        revision_id = str(uuid.uuid4())
        now = utc_now()
        content_hash = _content_hash(draft["payload"])

        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            # Supersede previous approved revisions for same target.
            self._db.execute(
                """
                UPDATE formal_revisions
                SET status = 'superseded'
                WHERE target_type = ? AND target_id = ? AND status = 'approved'
                """,
                (draft["target_type"], target_key),
            )
            self._db.execute(
                """
                INSERT INTO formal_revisions(
                    id, draft_id, target_type, target_id, branch_id, schema_id,
                    title, payload_json, content_hash, revision_no, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)
                """,
                (
                    revision_id,
                    draft_id,
                    draft["target_type"],
                    target_key,
                    draft["branch_id"],
                    draft["schema_id"],
                    draft["title"],
                    _stable_json(draft["payload"]),
                    content_hash,
                    revision_no,
                    now,
                ),
            )
            self._db.execute(
                """
                UPDATE content_drafts
                SET status = 'promoted', updated_at = ?
                WHERE id = ?
                """,
                (now, draft_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise

        return {
            "id": revision_id,
            "draft_id": draft_id,
            "target_type": draft["target_type"],
            "target_id": target_key,
            "branch_id": draft["branch_id"],
            "schema_id": draft["schema_id"],
            "title": draft["title"],
            "payload": draft["payload"],
            "content_hash": content_hash,
            "revision_no": revision_no,
            "status": "approved",
            "created_at": now,
        }

    def get(self, draft_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM content_drafts WHERE id = ?", (draft_id,)
        )
        if row is None:
            raise ValueError(f"draft not found: {draft_id}")
        return {
            "id": row["id"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "branch_id": row["branch_id"],
            "schema_id": row["schema_id"],
            "title": row["title"],
            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "validation_errors": json.loads(row["validation_errors_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_drafts(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if status is not None:
            if status not in DRAFT_STATUSES:
                raise ValueError(f"invalid status: {status}")
            rows = self._db.fetchall(
                """
                SELECT * FROM content_drafts
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT * FROM content_drafts
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [self.get(row["id"]) for row in rows]

    def list_revisions(
        self,
        *,
        target_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if target_type is not None:
            rows = self._db.fetchall(
                """
                SELECT * FROM formal_revisions
                WHERE target_type = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (target_type, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT * FROM formal_revisions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [
            {
                "id": row["id"],
                "draft_id": row["draft_id"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "branch_id": row["branch_id"],
                "schema_id": row["schema_id"],
                "title": row["title"],
                "payload": json.loads(row["payload_json"]),
                "content_hash": row["content_hash"],
                "revision_no": int(row["revision_no"]),
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_schemas(self) -> list[dict[str, Any]]:
        return [
            {"schema_id": key, "required": value.get("required", [])}
            for key, value in BUILTIN_SCHEMAS.items()
        ]
