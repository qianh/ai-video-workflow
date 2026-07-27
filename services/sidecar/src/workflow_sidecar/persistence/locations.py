"""Locations, location packs, spatial links, and props (M2-11).

Locations are stable identities; spatial production data lives in pack
revisions. Key props are trackable entities with appearance revisions.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import Database
from .timeutil import utc_now

EDITABLE = frozenset({"draft", "validated"})
LOCATION_TYPES = frozenset(
    {
        "interior",
        "exterior",
        "vehicle",
        "virtual",
        "transitional",
        "other",
    }
)
LINK_TYPES = frozenset(
    {
        "adjacent",
        "connected",
        "contains",
        "overlooks",
        "entrance_to",
        "other",
    }
)
VISIBILITY = frozenset({"visible", "hidden", "stored", "destroyed"})


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


def _as_list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _as_obj(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


class LocationService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- locations ---

    def create_location(
        self,
        *,
        branch_id: str,
        name: str,
        location_type: str = "interior",
        description: str | None = None,
        is_core: bool = False,
        slug: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        if location_type not in LOCATION_TYPES:
            raise ValueError(f"location_type must be one of {sorted(LOCATION_TYPES)}")
        loc_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO locations(
                    id, branch_id, slug, status, is_core, current_revision_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, NULL, ?, ?)
                """,
                (
                    loc_id,
                    branch_id,
                    slug.strip() if isinstance(slug, str) and slug.strip() else None,
                    1 if is_core else 0,
                    now,
                    now,
                ),
            )
            self._db.execute(
                """
                INSERT INTO location_revisions(
                    id, location_id, revision_no, status, name, location_type,
                    description, content_hash, notes, created_at
                ) VALUES (?, ?, 1, 'draft', ?, ?, ?, NULL, ?, ?)
                """,
                (rev_id, loc_id, name, location_type, description, notes, now),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_location(loc_id)

    def get_location(self, location_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM locations WHERE id = ?", (location_id,)
        )
        if row is None:
            raise ValueError(f"location not found: {location_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_location_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM location_revisions
                WHERE location_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (location_id,),
            )
            if latest:
                current = self.get_location_revision(latest["id"])
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "slug": row["slug"],
            "status": row["status"],
            "is_core": bool(row["is_core"]),
            "current_revision_id": row["current_revision_id"],
            "current_revision": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_location_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM location_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"location revision not found: {revision_id}")
        return {
            "id": row["id"],
            "location_id": row["location_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "name": row["name"],
            "location_type": row["location_type"],
            "description": row["description"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "contains_media_prompts": False,
        }

    def list_locations(
        self, *, branch_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if branch_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM locations
                WHERE branch_id = ? AND status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (branch_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM locations
                WHERE status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (limit,),
            )
        return [self.get_location(row["id"]) for row in rows]

    def approve_location_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_location_revision(revision_id)
        if rev["status"] not in {"draft", "validated"}:
            raise ValueError(f"cannot approve status: {rev['status']}")
        if not rev["name"].strip():
            raise ValueError("name is required")
        content_hash = _hash(
            {
                "name": rev["name"],
                "location_type": rev["location_type"],
                "description": rev["description"],
            }
        )
        now = utc_now()
        loc_id = rev["location_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE location_revisions
                SET status = 'superseded'
                WHERE location_id = ? AND status = 'approved' AND id != ?
                """,
                (loc_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE location_revisions
                SET status = 'approved', content_hash = ?
                WHERE id = ?
                """,
                (content_hash, revision_id),
            )
            self._db.execute(
                """
                UPDATE locations
                SET current_revision_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (revision_id, now, loc_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_location(loc_id)

    def mark_core(self, location_id: str, is_core: bool = True) -> dict[str, Any]:
        self.get_location(location_id)
        now = utc_now()
        self._db.execute(
            """
            UPDATE locations SET is_core = ?, updated_at = ? WHERE id = ?
            """,
            (1 if is_core else 0, now, location_id),
        )
        self._db.commit()
        return self.get_location(location_id)

    # --- location packs ---

    def create_pack(
        self,
        *,
        location_id: str,
        layout: dict[str, Any] | None = None,
        direction_axis: str | None = None,
        primary_view: str | None = None,
        camera_angles: list[Any] | None = None,
        entrances: list[Any] | None = None,
        furniture_anchors: list[Any] | None = None,
        day_variant: dict[str, Any] | None = None,
        night_variant: dict[str, Any] | None = None,
        reference_asset_ids: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        self.get_location(location_id)
        pack_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO location_packs(
                    id, location_id, status, current_revision_id,
                    confirmed_revision_id, created_at, updated_at
                ) VALUES (?, ?, 'active', NULL, NULL, ?, ?)
                """,
                (pack_id, location_id, now, now),
            )
            self._db.execute(
                """
                INSERT INTO location_pack_revisions(
                    id, pack_id, revision_no, status, layout_json,
                    direction_axis, primary_view, camera_angles_json,
                    entrances_json, furniture_anchors_json, day_variant_json,
                    night_variant_json, reference_asset_ids_json, content_hash,
                    notes, created_at, confirmed_at
                ) VALUES (?, ?, 1, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                          ?, ?, NULL)
                """,
                (
                    rev_id,
                    pack_id,
                    _stable_json(_as_obj(layout, "layout")),
                    direction_axis,
                    primary_view,
                    _stable_json(_as_list(camera_angles, "camera_angles")),
                    _stable_json(_as_list(entrances, "entrances")),
                    _stable_json(_as_list(furniture_anchors, "furniture_anchors")),
                    _stable_json(_as_obj(day_variant, "day_variant")),
                    _stable_json(_as_obj(night_variant, "night_variant")),
                    _stable_json(_as_list(reference_asset_ids or [], "reference_asset_ids")),
                    notes,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_pack(pack_id)

    def get_pack(self, pack_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM location_packs WHERE id = ?", (pack_id,)
        )
        if row is None:
            raise ValueError(f"location pack not found: {pack_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_pack_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM location_pack_revisions
                WHERE pack_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (pack_id,),
            )
            if latest:
                current = self.get_pack_revision(latest["id"])
        confirmed = None
        if row["confirmed_revision_id"]:
            confirmed = self.get_pack_revision(row["confirmed_revision_id"])
        return {
            "id": row["id"],
            "location_id": row["location_id"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "confirmed_revision_id": row["confirmed_revision_id"],
            "current_revision": current,
            "confirmed_revision": confirmed,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_pack_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM location_pack_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"location pack revision not found: {revision_id}")
        return {
            "id": row["id"],
            "pack_id": row["pack_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "layout": json.loads(row["layout_json"]),
            "direction_axis": row["direction_axis"],
            "primary_view": row["primary_view"],
            "camera_angles": json.loads(row["camera_angles_json"]),
            "entrances": json.loads(row["entrances_json"]),
            "furniture_anchors": json.loads(row["furniture_anchors_json"]),
            "day_variant": json.loads(row["day_variant_json"]),
            "night_variant": json.loads(row["night_variant_json"]),
            "reference_asset_ids": json.loads(row["reference_asset_ids_json"]),
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "confirmed_at": row["confirmed_at"],
            "prop_anchors": self.list_prop_anchors(revision_id),
            "contains_media_prompts": False,
        }

    def update_pack_revision(
        self,
        revision_id: str,
        *,
        layout: dict[str, Any] | None = None,
        direction_axis: str | None = None,
        primary_view: str | None = None,
        camera_angles: list[Any] | None = None,
        entrances: list[Any] | None = None,
        furniture_anchors: list[Any] | None = None,
        day_variant: dict[str, Any] | None = None,
        night_variant: dict[str, Any] | None = None,
        reference_asset_ids: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        rev = self.get_pack_revision(revision_id)
        if rev["status"] not in EDITABLE:
            raise ValueError(f"revision not editable: {rev['status']}")
        self._db.execute(
            """
            UPDATE location_pack_revisions
            SET layout_json = ?, direction_axis = ?, primary_view = ?,
                camera_angles_json = ?, entrances_json = ?,
                furniture_anchors_json = ?, day_variant_json = ?,
                night_variant_json = ?, reference_asset_ids_json = ?,
                notes = ?, status = 'draft', content_hash = NULL
            WHERE id = ?
            """,
            (
                _stable_json(
                    _as_obj(layout, "layout") if layout is not None else rev["layout"]
                ),
                direction_axis if direction_axis is not None else rev["direction_axis"],
                primary_view if primary_view is not None else rev["primary_view"],
                _stable_json(
                    _as_list(camera_angles, "camera_angles")
                    if camera_angles is not None
                    else rev["camera_angles"]
                ),
                _stable_json(
                    _as_list(entrances, "entrances")
                    if entrances is not None
                    else rev["entrances"]
                ),
                _stable_json(
                    _as_list(furniture_anchors, "furniture_anchors")
                    if furniture_anchors is not None
                    else rev["furniture_anchors"]
                ),
                _stable_json(
                    _as_obj(day_variant, "day_variant")
                    if day_variant is not None
                    else rev["day_variant"]
                ),
                _stable_json(
                    _as_obj(night_variant, "night_variant")
                    if night_variant is not None
                    else rev["night_variant"]
                ),
                _stable_json(
                    _as_list(reference_asset_ids, "reference_asset_ids")
                    if reference_asset_ids is not None
                    else rev["reference_asset_ids"]
                ),
                notes if notes is not None else rev["notes"],
                revision_id,
            ),
        )
        self._db.commit()
        return self.get_pack_revision(revision_id)

    def list_packs(
        self, *, location_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if location_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM location_packs
                WHERE location_id = ? AND status = 'active'
                ORDER BY created_at DESC LIMIT ?
                """,
                (location_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM location_packs
                WHERE status = 'active'
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        return [self.get_pack(row["id"]) for row in rows]

    def validate_pack(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_pack_revision(revision_id)
        if rev["status"] in {"confirmed", "superseded"}:
            raise ValueError(f"cannot validate status: {rev['status']}")
        errors = self._pack_errors(rev)
        status = "validated" if not errors else "draft"
        content_hash = None
        if not errors:
            content_hash = _hash(
                {
                    "layout": rev["layout"],
                    "direction_axis": rev["direction_axis"],
                    "primary_view": rev["primary_view"],
                    "camera_angles": rev["camera_angles"],
                    "entrances": rev["entrances"],
                    "day_variant": rev["day_variant"],
                    "night_variant": rev["night_variant"],
                    "prop_anchors": rev["prop_anchors"],
                }
            )
        self._db.execute(
            """
            UPDATE location_pack_revisions
            SET status = ?, content_hash = ?
            WHERE id = ?
            """,
            (status, content_hash, revision_id),
        )
        self._db.commit()
        result = self.get_pack_revision(revision_id)
        result["validation_errors"] = errors
        result["valid"] = not errors
        return result

    def approve_pack(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_pack_revision(revision_id)
        if rev["status"] == "draft":
            validated = self.validate_pack(revision_id)
            if not validated["valid"]:
                raise ValueError(
                    f"location pack validation failed: {validated['validation_errors'][0]}"
                )
            rev = self.get_pack_revision(revision_id)
        if rev["status"] != "validated":
            raise ValueError(f"cannot approve status: {rev['status']}")
        now = utc_now()
        pack_id = rev["pack_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE location_pack_revisions
                SET status = 'superseded'
                WHERE pack_id = ? AND status IN ('approved', 'confirmed')
                  AND id != ?
                """,
                (pack_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE location_pack_revisions
                SET status = 'approved' WHERE id = ?
                """,
                (revision_id,),
            )
            self._db.execute(
                """
                UPDATE location_packs
                SET current_revision_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (revision_id, now, pack_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_pack(pack_id)

    def confirm_pack(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_pack_revision(revision_id)
        if rev["status"] == "draft":
            self.approve_pack(revision_id)
            rev = self.get_pack_revision(revision_id)
        if rev["status"] == "validated":
            self.approve_pack(revision_id)
            rev = self.get_pack_revision(revision_id)
        if rev["status"] == "confirmed":
            return self.get_pack(rev["pack_id"])
        if rev["status"] != "approved":
            raise ValueError(f"cannot confirm status: {rev['status']}")
        errors = self._pack_errors(rev)
        if errors:
            raise ValueError(f"location pack confirmation failed: {errors[0]}")
        now = utc_now()
        pack_id = rev["pack_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE location_pack_revisions
                SET status = 'superseded'
                WHERE pack_id = ? AND status = 'confirmed' AND id != ?
                """,
                (pack_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE location_pack_revisions
                SET status = 'confirmed', confirmed_at = ?
                WHERE id = ?
                """,
                (now, revision_id),
            )
            self._db.execute(
                """
                UPDATE location_packs
                SET current_revision_id = ?, confirmed_revision_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (revision_id, revision_id, now, pack_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_pack(pack_id)

    def _pack_errors(self, rev: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not rev.get("layout"):
            errors.append("layout is required")
        if not (rev.get("primary_view") or "").strip():
            errors.append("primary_view is required")
        if not (rev.get("direction_axis") or "").strip():
            errors.append("direction_axis is required")
        if not rev.get("camera_angles"):
            errors.append("at least one camera_angle is required")
        if not rev.get("day_variant") and not rev.get("night_variant"):
            errors.append("day_variant or night_variant is required")
        return errors

    # --- spatial links ---

    def add_spatial_link(
        self,
        *,
        branch_id: str,
        source_location_id: str,
        target_location_id: str,
        link_type: str,
        description: str | None = None,
        bidirectional: bool = True,
    ) -> dict[str, Any]:
        if source_location_id == target_location_id:
            raise ValueError("source and target locations must differ")
        self.get_location(source_location_id)
        self.get_location(target_location_id)
        if link_type not in LINK_TYPES:
            raise ValueError(f"link_type must be one of {sorted(LINK_TYPES)}")
        link_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO location_spatial_links(
                id, branch_id, source_location_id, target_location_id,
                link_type, description, bidirectional, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                link_id,
                branch_id,
                source_location_id,
                target_location_id,
                link_type,
                description,
                1 if bidirectional else 0,
                now,
            ),
        )
        self._db.commit()
        return self.get_spatial_link(link_id)

    def get_spatial_link(self, link_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM location_spatial_links WHERE id = ?", (link_id,)
        )
        if row is None:
            raise ValueError(f"spatial link not found: {link_id}")
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "source_location_id": row["source_location_id"],
            "target_location_id": row["target_location_id"],
            "link_type": row["link_type"],
            "description": row["description"],
            "bidirectional": bool(row["bidirectional"]),
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def list_spatial_links(
        self, *, branch_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if branch_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM location_spatial_links
                WHERE branch_id = ? AND status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (branch_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM location_spatial_links
                WHERE status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (limit,),
            )
        return [self.get_spatial_link(row["id"]) for row in rows]

    # --- props ---

    def create_prop(
        self,
        *,
        branch_id: str,
        name: str,
        appearance: str,
        owner_character_id: str | None = None,
        state_notes: str | None = None,
        is_key_prop: bool = True,
        slug: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        appearance = appearance.strip()
        if not name or not appearance:
            raise ValueError("name and appearance are required")
        if owner_character_id is not None:
            row = self._db.fetchone(
                "SELECT id FROM characters WHERE id = ?", (owner_character_id,)
            )
            if row is None:
                raise ValueError(f"character not found: {owner_character_id}")
        prop_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO props(
                    id, branch_id, slug, status, is_key_prop, current_revision_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, NULL, ?, ?)
                """,
                (
                    prop_id,
                    branch_id,
                    slug.strip() if isinstance(slug, str) and slug.strip() else None,
                    1 if is_key_prop else 0,
                    now,
                    now,
                ),
            )
            self._db.execute(
                """
                INSERT INTO prop_revisions(
                    id, prop_id, revision_no, status, name, appearance,
                    owner_character_id, state_notes, content_hash, notes, created_at
                ) VALUES (?, ?, 1, 'draft', ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    rev_id,
                    prop_id,
                    name,
                    appearance,
                    owner_character_id,
                    state_notes,
                    notes,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_prop(prop_id)

    def get_prop(self, prop_id: str) -> dict[str, Any]:
        row = self._db.fetchone("SELECT * FROM props WHERE id = ?", (prop_id,))
        if row is None:
            raise ValueError(f"prop not found: {prop_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_prop_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM prop_revisions
                WHERE prop_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (prop_id,),
            )
            if latest:
                current = self.get_prop_revision(latest["id"])
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "slug": row["slug"],
            "status": row["status"],
            "is_key_prop": bool(row["is_key_prop"]),
            "current_revision_id": row["current_revision_id"],
            "current_revision": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_prop_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM prop_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"prop revision not found: {revision_id}")
        return {
            "id": row["id"],
            "prop_id": row["prop_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "name": row["name"],
            "appearance": row["appearance"],
            "owner_character_id": row["owner_character_id"],
            "state_notes": row["state_notes"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "contains_media_prompts": False,
        }

    def list_props(
        self, *, branch_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if branch_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM props
                WHERE branch_id = ? AND status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (branch_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM props
                WHERE status = 'active'
                ORDER BY created_at ASC LIMIT ?
                """,
                (limit,),
            )
        return [self.get_prop(row["id"]) for row in rows]

    def approve_prop_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_prop_revision(revision_id)
        if rev["status"] not in {"draft", "validated"}:
            raise ValueError(f"cannot approve status: {rev['status']}")
        if not rev["name"].strip() or not rev["appearance"].strip():
            raise ValueError("name and appearance are required")
        content_hash = _hash(
            {
                "name": rev["name"],
                "appearance": rev["appearance"],
                "owner_character_id": rev["owner_character_id"],
                "state_notes": rev["state_notes"],
            }
        )
        now = utc_now()
        prop_id = rev["prop_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE prop_revisions
                SET status = 'superseded'
                WHERE prop_id = ? AND status = 'approved' AND id != ?
                """,
                (prop_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE prop_revisions
                SET status = 'approved', content_hash = ?
                WHERE id = ?
                """,
                (content_hash, revision_id),
            )
            self._db.execute(
                """
                UPDATE props
                SET current_revision_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (revision_id, now, prop_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_prop(prop_id)

    # --- prop anchors in packs ---

    def anchor_prop(
        self,
        *,
        location_pack_revision_id: str,
        prop_id: str,
        anchor_label: str,
        position: dict[str, Any] | None = None,
        visibility: str = "visible",
    ) -> dict[str, Any]:
        rev = self.get_pack_revision(location_pack_revision_id)
        if rev["status"] not in EDITABLE:
            raise ValueError(f"cannot anchor prop in status: {rev['status']}")
        self.get_prop(prop_id)
        anchor_label = anchor_label.strip()
        if not anchor_label:
            raise ValueError("anchor_label is required")
        if visibility not in VISIBILITY:
            raise ValueError(f"visibility must be one of {sorted(VISIBILITY)}")
        anchor_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO location_prop_anchors(
                    id, location_pack_revision_id, prop_id, anchor_label,
                    position_json, visibility, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    anchor_id,
                    location_pack_revision_id,
                    prop_id,
                    anchor_label,
                    _stable_json(_as_obj(position, "position")),
                    visibility,
                    now,
                ),
            )
            self._db.execute(
                """
                UPDATE location_pack_revisions
                SET status = 'draft', content_hash = NULL
                WHERE id = ?
                """,
                (location_pack_revision_id,),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_prop_anchor(anchor_id)

    def get_prop_anchor(self, anchor_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM location_prop_anchors WHERE id = ?", (anchor_id,)
        )
        if row is None:
            raise ValueError(f"prop anchor not found: {anchor_id}")
        return {
            "id": row["id"],
            "location_pack_revision_id": row["location_pack_revision_id"],
            "prop_id": row["prop_id"],
            "anchor_label": row["anchor_label"],
            "position": json.loads(row["position_json"]),
            "visibility": row["visibility"],
            "created_at": row["created_at"],
        }

    def list_prop_anchors(self, revision_id: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM location_prop_anchors
            WHERE location_pack_revision_id = ?
            ORDER BY created_at ASC
            """,
            (revision_id,),
        )
        return [self.get_prop_anchor(row["id"]) for row in rows]

    # --- gates / overview ---

    def production_gate(self, location_id: str) -> dict[str, Any]:
        loc = self.get_location(location_id)
        packs = self.list_packs(location_id=location_id, limit=20)
        confirmed = [
            p
            for p in packs
            if p.get("confirmed_revision_id")
            and p.get("confirmed_revision")
            and p["confirmed_revision"]["status"] == "confirmed"
        ]
        required = bool(loc["is_core"])
        ready = bool(confirmed) if required else True
        return {
            "location_id": location_id,
            "is_core": loc["is_core"],
            "location_pack_required": required,
            "ready_for_production": ready,
            "confirmed_pack_id": confirmed[0]["id"] if confirmed else None,
            "confirmed_revision_id": (
                confirmed[0]["confirmed_revision_id"] if confirmed else None
            ),
            "blocker": (
                None
                if ready
                else "core location pack is not confirmed"
            ),
        }

    def world_overview(self, branch_id: str) -> dict[str, Any]:
        return {
            "branch_id": branch_id,
            "locations": self.list_locations(branch_id=branch_id),
            "packs": self.list_packs(limit=50),
            "spatial_links": self.list_spatial_links(branch_id=branch_id),
            "props": self.list_props(branch_id=branch_id),
        }
