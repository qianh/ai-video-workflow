"""Visual bible and director preset three-level inheritance (M2-13).

Levels: project → episode → shot.
Lower scopes store diffs only. Fields listed in an ancestor's locked_fields
are hard constraints and cannot be overridden by children.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Any

from .database import Database
from .timeutil import utc_now

SCOPE_LEVELS = ("project", "episode", "shot")
SCOPE_RANK = {level: index for index, level in enumerate(SCOPE_LEVELS)}
EDITABLE = frozenset({"draft", "validated"})


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


def _as_str_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} items must be non-empty strings")
        out.append(item.strip())
    return out


def _as_obj(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def deep_merge(base: Any, override: Any) -> Any:
    """Merge override onto base; dicts recurse, other values replace."""

    if isinstance(base, dict) and isinstance(override, dict):
        result = copy.deepcopy(base)
        for key, value in override.items():
            if key in result:
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result
    return copy.deepcopy(override)


def merge_with_locks(
    layers: list[dict[str, Any]],
) -> dict[str, Any]:
    """layers ordered base→top. Each has payload, locked_fields, scope_level, id."""

    effective: dict[str, Any] = {}
    provenance: dict[str, dict[str, str]] = {}
    locked: set[str] = set()
    for layer in layers:
        payload = layer.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        for key, value in payload.items():
            if key in locked:
                continue
            if key in effective and isinstance(effective[key], dict) and isinstance(
                value, dict
            ):
                effective[key] = deep_merge(effective[key], value)
            else:
                effective[key] = copy.deepcopy(value)
            provenance[key] = {
                "scope_level": layer["scope_level"],
                "revision_id": layer["id"],
            }
        locked |= set(layer.get("locked_fields") or [])
    return {
        "effective": effective,
        "provenance": provenance,
        "locked_fields": sorted(locked),
        "layers": [
            {
                "revision_id": layer["id"],
                "scope_level": layer["scope_level"],
                "scope_ref": layer.get("scope_ref"),
                "locked_fields": layer.get("locked_fields") or [],
            }
            for layer in layers
        ],
    }


class DirectorService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- visual bible ---

    def create_visual_bible(
        self,
        *,
        branch_id: str,
        name: str,
        style_name: str,
        payload: dict[str, Any] | None = None,
        locked_fields: list[str] | None = None,
        scope_level: str = "project",
        scope_ref: str | None = None,
        parent_revision_id: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        style_name = style_name.strip()
        if not name or not style_name:
            raise ValueError("name and style_name are required")
        self._validate_scope(scope_level, scope_ref)
        payload = _as_obj(payload, "payload")
        # Ensure style_name is also in payload for merge provenance.
        if "style_name" not in payload:
            payload = {**payload, "style_name": style_name}
        locked = _as_str_list(locked_fields, "locked_fields")
        if parent_revision_id is not None:
            self.get_visual_revision(parent_revision_id)

        bible_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO visual_bibles(
                    id, branch_id, name, status, current_revision_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'active', NULL, ?, ?)
                """,
                (bible_id, branch_id, name, now, now),
            )
            self._db.execute(
                """
                INSERT INTO visual_bible_revisions(
                    id, bible_id, revision_no, scope_level, scope_ref, status,
                    style_name, payload_json, locked_fields_json, parent_revision_id,
                    content_hash, notes, created_at
                ) VALUES (?, ?, 1, ?, ?, 'draft', ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    rev_id,
                    bible_id,
                    scope_level,
                    scope_ref,
                    style_name,
                    _stable_json(payload),
                    _stable_json(locked),
                    parent_revision_id,
                    notes,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_visual_bible(bible_id)

    def add_visual_revision(
        self,
        *,
        bible_id: str,
        scope_level: str,
        scope_ref: str | None = None,
        style_name: str | None = None,
        payload: dict[str, Any] | None = None,
        locked_fields: list[str] | None = None,
        parent_revision_id: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        bible = self.get_visual_bible(bible_id)
        self._validate_scope(scope_level, scope_ref)
        payload = _as_obj(payload, "payload")
        locked = _as_str_list(locked_fields, "locked_fields")
        if parent_revision_id is not None:
            self.get_visual_revision(parent_revision_id)
        # Default style from current if not provided.
        if not style_name:
            current = bible.get("current_revision") or {}
            style_name = current.get("style_name") or "unnamed"
        style_name = style_name.strip()
        if not style_name:
            raise ValueError("style_name is required")
        if "style_name" not in payload:
            payload = {**payload, "style_name": style_name}

        row = self._db.fetchone(
            """
            SELECT COALESCE(MAX(revision_no), 0) AS m
            FROM visual_bible_revisions WHERE bible_id = ?
            """,
            (bible_id,),
        )
        revision_no = int(row["m"]) + 1 if row else 1
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO visual_bible_revisions(
                id, bible_id, revision_no, scope_level, scope_ref, status,
                style_name, payload_json, locked_fields_json, parent_revision_id,
                content_hash, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                rev_id,
                bible_id,
                revision_no,
                scope_level,
                scope_ref,
                style_name,
                _stable_json(payload),
                _stable_json(locked),
                parent_revision_id,
                notes,
                now,
            ),
        )
        self._db.commit()
        return self.get_visual_revision(rev_id)

    def get_visual_bible(self, bible_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM visual_bibles WHERE id = ?", (bible_id,)
        )
        if row is None:
            raise ValueError(f"visual bible not found: {bible_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_visual_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM visual_bible_revisions
                WHERE bible_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (bible_id,),
            )
            if latest:
                current = self.get_visual_revision(latest["id"])
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "name": row["name"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "current_revision": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_visual_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM visual_bible_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"visual bible revision not found: {revision_id}")
        return {
            "id": row["id"],
            "bible_id": row["bible_id"],
            "revision_no": int(row["revision_no"]),
            "scope_level": row["scope_level"],
            "scope_ref": row["scope_ref"],
            "status": row["status"],
            "style_name": row["style_name"],
            "payload": json.loads(row["payload_json"]),
            "locked_fields": json.loads(row["locked_fields_json"]),
            "parent_revision_id": row["parent_revision_id"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "contains_media_prompts": True,
        }

    def list_visual_bibles(
        self, *, branch_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if branch_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM visual_bibles
                WHERE branch_id = ? AND status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (branch_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM visual_bibles
                WHERE status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (limit,),
            )
        return [self.get_visual_bible(row["id"]) for row in rows]

    def list_visual_revisions(
        self, *, bible_id: str, scope_level: str | None = None
    ) -> list[dict[str, Any]]:
        if scope_level is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM visual_bible_revisions
                WHERE bible_id = ? AND scope_level = ?
                ORDER BY revision_no ASC
                """,
                (bible_id, scope_level),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM visual_bible_revisions
                WHERE bible_id = ?
                ORDER BY revision_no ASC
                """,
                (bible_id,),
            )
        return [self.get_visual_revision(row["id"]) for row in rows]

    def approve_visual_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_visual_revision(revision_id)
        if rev["status"] not in {"draft", "validated"}:
            raise ValueError(f"cannot approve status: {rev['status']}")
        if not rev["style_name"].strip():
            raise ValueError("style_name is required")
        content_hash = _hash(
            {
                "scope_level": rev["scope_level"],
                "scope_ref": rev["scope_ref"],
                "style_name": rev["style_name"],
                "payload": rev["payload"],
                "locked_fields": rev["locked_fields"],
            }
        )
        now = utc_now()
        bible_id = rev["bible_id"]
        impact = None
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            # Supersede previous approved at same scope only.
            self._db.execute(
                """
                UPDATE visual_bible_revisions
                SET status = 'superseded'
                WHERE bible_id = ?
                  AND scope_level = ?
                  AND IFNULL(scope_ref, '') = IFNULL(?, '')
                  AND status = 'approved'
                  AND id != ?
                """,
                (bible_id, rev["scope_level"], rev["scope_ref"], revision_id),
            )
            self._db.execute(
                """
                UPDATE visual_bible_revisions
                SET status = 'approved', content_hash = ?
                WHERE id = ?
                """,
                (content_hash, revision_id),
            )
            # Project-level becomes current for the bible entity.
            if rev["scope_level"] == "project":
                self._db.execute(
                    """
                    UPDATE visual_bibles
                    SET current_revision_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (revision_id, now, bible_id),
                )
                impact = self._write_impact(
                    kind="visual_bible",
                    base_revision_id=revision_id,
                    bible_id=bible_id,
                )
            else:
                self._db.execute(
                    """
                    UPDATE visual_bibles SET updated_at = ? WHERE id = ?
                    """,
                    (now, bible_id),
                )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        result = self.get_visual_bible(bible_id)
        result["approved_revision"] = self.get_visual_revision(revision_id)
        if impact is not None:
            result["impact_report"] = impact
        return result

    def resolve_visual(
        self,
        *,
        bible_id: str,
        episode_ref: str | None = None,
        shot_ref: str | None = None,
        approved_only: bool = True,
    ) -> dict[str, Any]:
        layers = self._collect_visual_layers(
            bible_id=bible_id,
            episode_ref=episode_ref,
            shot_ref=shot_ref,
            approved_only=approved_only,
        )
        if not layers:
            raise ValueError("no visual bible layers found to resolve")
        merged = merge_with_locks(layers)
        return {
            "bible_id": bible_id,
            "episode_ref": episode_ref,
            "shot_ref": shot_ref,
            **merged,
            "inheritance_levels": ["project", "episode", "shot"],
        }

    # --- director presets ---

    def create_director_preset(
        self,
        *,
        branch_id: str,
        name: str,
        payload: dict[str, Any] | None = None,
        locked_fields: list[str] | None = None,
        scope_level: str = "project",
        scope_ref: str | None = None,
        parent_revision_id: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        self._validate_scope(scope_level, scope_ref)
        payload = _as_obj(payload, "payload")
        if not payload:
            raise ValueError("payload must be a non-empty object")
        locked = _as_str_list(locked_fields, "locked_fields")
        if parent_revision_id is not None:
            self.get_director_revision(parent_revision_id)

        preset_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO director_presets(
                    id, branch_id, name, status, current_revision_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'active', NULL, ?, ?)
                """,
                (preset_id, branch_id, name, now, now),
            )
            self._db.execute(
                """
                INSERT INTO director_preset_revisions(
                    id, preset_id, revision_no, scope_level, scope_ref, status,
                    payload_json, locked_fields_json, parent_revision_id,
                    content_hash, notes, created_at
                ) VALUES (?, ?, 1, ?, ?, 'draft', ?, ?, ?, NULL, ?, ?)
                """,
                (
                    rev_id,
                    preset_id,
                    scope_level,
                    scope_ref,
                    _stable_json(payload),
                    _stable_json(locked),
                    parent_revision_id,
                    notes,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_director_preset(preset_id)

    def add_director_revision(
        self,
        *,
        preset_id: str,
        scope_level: str,
        scope_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        locked_fields: list[str] | None = None,
        parent_revision_id: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        self.get_director_preset(preset_id)
        self._validate_scope(scope_level, scope_ref)
        payload = _as_obj(payload, "payload")
        if not payload:
            raise ValueError("payload must be a non-empty object")
        locked = _as_str_list(locked_fields, "locked_fields")
        if parent_revision_id is not None:
            self.get_director_revision(parent_revision_id)
        row = self._db.fetchone(
            """
            SELECT COALESCE(MAX(revision_no), 0) AS m
            FROM director_preset_revisions WHERE preset_id = ?
            """,
            (preset_id,),
        )
        revision_no = int(row["m"]) + 1 if row else 1
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO director_preset_revisions(
                id, preset_id, revision_no, scope_level, scope_ref, status,
                payload_json, locked_fields_json, parent_revision_id,
                content_hash, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, NULL, ?, ?)
            """,
            (
                rev_id,
                preset_id,
                revision_no,
                scope_level,
                scope_ref,
                _stable_json(payload),
                _stable_json(locked),
                parent_revision_id,
                notes,
                now,
            ),
        )
        self._db.commit()
        return self.get_director_revision(rev_id)

    def get_director_preset(self, preset_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM director_presets WHERE id = ?", (preset_id,)
        )
        if row is None:
            raise ValueError(f"director preset not found: {preset_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_director_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM director_preset_revisions
                WHERE preset_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (preset_id,),
            )
            if latest:
                current = self.get_director_revision(latest["id"])
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "name": row["name"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "current_revision": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_director_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM director_preset_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"director preset revision not found: {revision_id}")
        return {
            "id": row["id"],
            "preset_id": row["preset_id"],
            "revision_no": int(row["revision_no"]),
            "scope_level": row["scope_level"],
            "scope_ref": row["scope_ref"],
            "status": row["status"],
            "payload": json.loads(row["payload_json"]),
            "locked_fields": json.loads(row["locked_fields_json"]),
            "parent_revision_id": row["parent_revision_id"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "contains_media_prompts": False,
        }

    def list_director_presets(
        self, *, branch_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if branch_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM director_presets
                WHERE branch_id = ? AND status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (branch_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM director_presets
                WHERE status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (limit,),
            )
        return [self.get_director_preset(row["id"]) for row in rows]

    def approve_director_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_director_revision(revision_id)
        if rev["status"] not in {"draft", "validated"}:
            raise ValueError(f"cannot approve status: {rev['status']}")
        if not rev["payload"]:
            raise ValueError("payload is required")
        content_hash = _hash(
            {
                "scope_level": rev["scope_level"],
                "scope_ref": rev["scope_ref"],
                "payload": rev["payload"],
                "locked_fields": rev["locked_fields"],
            }
        )
        now = utc_now()
        preset_id = rev["preset_id"]
        impact = None
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE director_preset_revisions
                SET status = 'superseded'
                WHERE preset_id = ?
                  AND scope_level = ?
                  AND IFNULL(scope_ref, '') = IFNULL(?, '')
                  AND status = 'approved'
                  AND id != ?
                """,
                (preset_id, rev["scope_level"], rev["scope_ref"], revision_id),
            )
            self._db.execute(
                """
                UPDATE director_preset_revisions
                SET status = 'approved', content_hash = ?
                WHERE id = ?
                """,
                (content_hash, revision_id),
            )
            if rev["scope_level"] == "project":
                self._db.execute(
                    """
                    UPDATE director_presets
                    SET current_revision_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (revision_id, now, preset_id),
                )
                impact = self._write_impact(
                    kind="director_preset",
                    base_revision_id=revision_id,
                    preset_id=preset_id,
                )
            else:
                self._db.execute(
                    """
                    UPDATE director_presets SET updated_at = ? WHERE id = ?
                    """,
                    (now, preset_id),
                )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        result = self.get_director_preset(preset_id)
        result["approved_revision"] = self.get_director_revision(revision_id)
        if impact is not None:
            result["impact_report"] = impact
        return result

    def resolve_director(
        self,
        *,
        preset_id: str,
        episode_ref: str | None = None,
        shot_ref: str | None = None,
        approved_only: bool = True,
    ) -> dict[str, Any]:
        layers = self._collect_director_layers(
            preset_id=preset_id,
            episode_ref=episode_ref,
            shot_ref=shot_ref,
            approved_only=approved_only,
        )
        if not layers:
            raise ValueError("no director preset layers found to resolve")
        merged = merge_with_locks(layers)
        return {
            "preset_id": preset_id,
            "episode_ref": episode_ref,
            "shot_ref": shot_ref,
            **merged,
            "inheritance_levels": ["project", "episode", "shot"],
        }

    def overview(self, branch_id: str) -> dict[str, Any]:
        bibles = self.list_visual_bibles(branch_id=branch_id)
        presets = self.list_director_presets(branch_id=branch_id)
        return {
            "branch_id": branch_id,
            "visual_bibles": bibles,
            "director_presets": presets,
            "inheritance_levels": list(SCOPE_LEVELS),
        }

    # --- internals ---

    def _validate_scope(self, scope_level: str, scope_ref: str | None) -> None:
        if scope_level not in SCOPE_RANK:
            raise ValueError(f"scope_level must be one of {list(SCOPE_LEVELS)}")
        if scope_level == "project":
            if scope_ref is not None and str(scope_ref).strip():
                raise ValueError("project scope must not set scope_ref")
        else:
            if not isinstance(scope_ref, str) or not scope_ref.strip():
                raise ValueError(f"{scope_level} scope requires scope_ref")

    def _pick_layer(
        self,
        revisions: list[dict[str, Any]],
        *,
        scope_level: str,
        scope_ref: str | None,
        approved_only: bool,
    ) -> dict[str, Any] | None:
        candidates = [
            r
            for r in revisions
            if r["scope_level"] == scope_level
            and (r.get("scope_ref") or None) == (scope_ref or None)
            and (not approved_only or r["status"] == "approved")
        ]
        if not candidates:
            return None
        # Highest revision_no wins.
        candidates.sort(key=lambda r: r["revision_no"], reverse=True)
        return candidates[0]

    def _collect_visual_layers(
        self,
        *,
        bible_id: str,
        episode_ref: str | None,
        shot_ref: str | None,
        approved_only: bool,
    ) -> list[dict[str, Any]]:
        revs = self.list_visual_revisions(bible_id=bible_id)
        layers: list[dict[str, Any]] = []
        project = self._pick_layer(
            revs, scope_level="project", scope_ref=None, approved_only=approved_only
        )
        if project:
            layers.append(project)
        if episode_ref:
            ep = self._pick_layer(
                revs,
                scope_level="episode",
                scope_ref=episode_ref,
                approved_only=approved_only,
            )
            if ep:
                layers.append(ep)
        if shot_ref:
            sh = self._pick_layer(
                revs,
                scope_level="shot",
                scope_ref=shot_ref,
                approved_only=approved_only,
            )
            if sh:
                layers.append(sh)
        return layers

    def _collect_director_layers(
        self,
        *,
        preset_id: str,
        episode_ref: str | None,
        shot_ref: str | None,
        approved_only: bool,
    ) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM director_preset_revisions
            WHERE preset_id = ?
            ORDER BY revision_no ASC
            """,
            (preset_id,),
        )
        revs = [self.get_director_revision(row["id"]) for row in rows]
        layers: list[dict[str, Any]] = []
        project = self._pick_layer(
            revs, scope_level="project", scope_ref=None, approved_only=approved_only
        )
        if project:
            layers.append(project)
        if episode_ref:
            ep = self._pick_layer(
                revs,
                scope_level="episode",
                scope_ref=episode_ref,
                approved_only=approved_only,
            )
            if ep:
                layers.append(ep)
        if shot_ref:
            sh = self._pick_layer(
                revs,
                scope_level="shot",
                scope_ref=shot_ref,
                approved_only=approved_only,
            )
            if sh:
                layers.append(sh)
        return layers

    def _write_impact(
        self,
        *,
        kind: str,
        base_revision_id: str,
        bible_id: str | None = None,
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        affected: list[str] = []
        if kind == "visual_bible" and bible_id:
            rows = self._db.fetchall(
                """
                SELECT id FROM visual_bible_revisions
                WHERE bible_id = ?
                  AND scope_level IN ('episode', 'shot')
                  AND status IN ('draft', 'approved')
                """,
                (bible_id,),
            )
            affected = [row["id"] for row in rows]
        if kind == "director_preset" and preset_id:
            rows = self._db.fetchall(
                """
                SELECT id FROM director_preset_revisions
                WHERE preset_id = ?
                  AND scope_level IN ('episode', 'shot')
                  AND status IN ('draft', 'approved')
                """,
                (preset_id,),
            )
            affected = [row["id"] for row in rows]
        report_id = str(uuid.uuid4())
        now = utc_now()
        summary = (
            f"{kind} project revision {base_revision_id[:8]} updated; "
            f"{len(affected)} child scope revision(s) may need review"
        )
        self._db.execute(
            """
            INSERT INTO inheritance_impact_reports(
                id, kind, base_revision_id, affected_revision_ids_json,
                summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                kind,
                base_revision_id,
                _stable_json(affected),
                summary,
                now,
            ),
        )
        return {
            "id": report_id,
            "kind": kind,
            "base_revision_id": base_revision_id,
            "affected_revision_ids": affected,
            "summary": summary,
            "created_at": now,
        }
