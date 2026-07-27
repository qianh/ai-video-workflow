"""Character identity packs and look candidates (M2-10).

Reuses the M0-05 Grok image_gen contract when WORKFLOW_ENABLE_GROK_LOOKS=1
and grok is on PATH; otherwise generates project-local mock look assets so
the confirmation gate can be exercised offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable

from .characters import CharacterService
from .database import Database
from .timeutil import utc_now

EDITABLE = frozenset({"draft", "validated"})
PACK_STATUSES = frozenset(
    {"draft", "validated", "approved", "confirmed", "superseded"}
)
CANDIDATE_STATUSES = frozenset(
    {"generated", "selected", "rejected", "archived"}
)

# Minimal valid 1x1 JPEG for offline mock looks.
_MOCK_JPEG = bytes(
    [
        0xFF,
        0xD8,
        0xFF,
        0xE0,
        0x00,
        0x10,
        0x4A,
        0x46,
        0x49,
        0x46,
        0x00,
        0x01,
        0x01,
        0x00,
        0x00,
        0x01,
        0x00,
        0x01,
        0x00,
        0x00,
        0xFF,
        0xDB,
        0x00,
        0x43,
        0x00,
        0x08,
        0x06,
        0x06,
        0x07,
        0x06,
        0x05,
        0x08,
        0x07,
        0x07,
        0x07,
        0x09,
        0x09,
        0x08,
        0x0A,
        0x0C,
        0x14,
        0x0D,
        0x0C,
        0x0B,
        0x0B,
        0x0C,
        0x19,
        0x12,
        0x13,
        0x0F,
        0x14,
        0x1D,
        0x1A,
        0x1F,
        0x1E,
        0x1D,
        0x1A,
        0x1C,
        0x1C,
        0x20,
        0x24,
        0x2E,
        0x27,
        0x20,
        0x22,
        0x2C,
        0x23,
        0x1C,
        0x1C,
        0x28,
        0x37,
        0x29,
        0x2C,
        0x30,
        0x31,
        0x34,
        0x34,
        0x34,
        0x1F,
        0x27,
        0x39,
        0x3D,
        0x38,
        0x32,
        0x3C,
        0x2E,
        0x33,
        0x34,
        0x32,
        0xFF,
        0xC0,
        0x00,
        0x0B,
        0x08,
        0x00,
        0x01,
        0x00,
        0x01,
        0x01,
        0x01,
        0x11,
        0x00,
        0xFF,
        0xC4,
        0x00,
        0x14,
        0x00,
        0x01,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x08,
        0xFF,
        0xC4,
        0x00,
        0x14,
        0x10,
        0x01,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0x00,
        0xFF,
        0xDA,
        0x00,
        0x08,
        0x01,
        0x01,
        0x00,
        0x00,
        0x3F,
        0x00,
        0x7F,
        0xFF,
        0xD9,
    ]
)


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


LookGenerator = Callable[[Path, str, str, str | None], dict[str, Any]]


class IdentityPackService:
    def __init__(
        self,
        db: Database,
        project_root: Path,
        *,
        look_generator: LookGenerator | None = None,
    ) -> None:
        self._db = db
        self._root = Path(project_root)
        self._characters = CharacterService(db)
        self._look_generator = look_generator or self._default_look_generator

    # --- packs ---

    def create_pack(
        self,
        *,
        character_id: str,
        positive_prompt: str = "",
        negative_prompt: str = "",
        height_cm: float | None = None,
        proportion_notes: str | None = None,
        voice_profile_id: str | None = None,
        multi_view_asset_ids: list[str] | None = None,
        shot_size_asset_ids: list[str] | None = None,
        expression_asset_ids: list[str] | None = None,
        outfit_asset_ids: list[str] | None = None,
        reference_priority: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        character = self._characters.get_character(character_id)
        if character["status"] != "active":
            raise ValueError("character is not active")
        # Prefer approved character revision name for default prompt.
        name = ""
        if character.get("current_revision"):
            name = character["current_revision"].get("name") or ""
        if not positive_prompt.strip() and name:
            appearance = character["current_revision"].get("appearance_rules") or ""
            positive_prompt = f"character look sheet of {name}, {appearance}".strip(", ")

        pack_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO character_identity_packs(
                    id, character_id, status, current_revision_id,
                    confirmed_revision_id, created_at, updated_at
                ) VALUES (?, ?, 'active', NULL, NULL, ?, ?)
                """,
                (pack_id, character_id, now, now),
            )
            self._db.execute(
                """
                INSERT INTO character_identity_pack_revisions(
                    id, pack_id, revision_no, status,
                    multi_view_asset_ids_json, shot_size_asset_ids_json,
                    expression_asset_ids_json, outfit_asset_ids_json,
                    positive_prompt, negative_prompt, reference_priority_json,
                    height_cm, proportion_notes, voice_profile_id,
                    selected_candidate_id, content_hash, notes, created_at,
                    confirmed_at
                ) VALUES (?, ?, 1, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                          NULL, ?, ?, NULL)
                """,
                (
                    rev_id,
                    pack_id,
                    _stable_json(_as_str_list(multi_view_asset_ids, "multi_view_asset_ids")),
                    _stable_json(_as_str_list(shot_size_asset_ids, "shot_size_asset_ids")),
                    _stable_json(
                        _as_str_list(expression_asset_ids, "expression_asset_ids")
                    ),
                    _stable_json(_as_str_list(outfit_asset_ids, "outfit_asset_ids")),
                    positive_prompt.strip() if isinstance(positive_prompt, str) else "",
                    negative_prompt.strip() if isinstance(negative_prompt, str) else "",
                    _stable_json(_as_str_list(reference_priority, "reference_priority")),
                    height_cm,
                    proportion_notes,
                    voice_profile_id,
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
            "SELECT * FROM character_identity_packs WHERE id = ?", (pack_id,)
        )
        if row is None:
            raise ValueError(f"identity pack not found: {pack_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM character_identity_pack_revisions
                WHERE pack_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (pack_id,),
            )
            if latest:
                current = self.get_revision(latest["id"])
        confirmed = None
        if row["confirmed_revision_id"]:
            confirmed = self.get_revision(row["confirmed_revision_id"])
        return {
            "id": row["id"],
            "character_id": row["character_id"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "confirmed_revision_id": row["confirmed_revision_id"],
            "current_revision": current,
            "confirmed_revision": confirmed,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "contains_media_prompts": True,  # identity packs own look prompts
        }

    def list_packs(
        self, *, character_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if character_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM character_identity_packs
                WHERE character_id = ? AND status = 'active'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (character_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM character_identity_packs
                WHERE status = 'active'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [self.get_pack(row["id"]) for row in rows]

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM character_identity_pack_revisions WHERE id = ?",
            (revision_id,),
        )
        if row is None:
            raise ValueError(f"identity pack revision not found: {revision_id}")
        return {
            "id": row["id"],
            "pack_id": row["pack_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "multi_view_asset_ids": json.loads(row["multi_view_asset_ids_json"]),
            "shot_size_asset_ids": json.loads(row["shot_size_asset_ids_json"]),
            "expression_asset_ids": json.loads(row["expression_asset_ids_json"]),
            "outfit_asset_ids": json.loads(row["outfit_asset_ids_json"]),
            "positive_prompt": row["positive_prompt"],
            "negative_prompt": row["negative_prompt"],
            "reference_priority": json.loads(row["reference_priority_json"]),
            "height_cm": row["height_cm"],
            "proportion_notes": row["proportion_notes"],
            "voice_profile_id": row["voice_profile_id"],
            "selected_candidate_id": row["selected_candidate_id"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "confirmed_at": row["confirmed_at"],
            "look_candidates": self.list_candidates(revision_id),
        }

    def update_revision(
        self,
        revision_id: str,
        *,
        positive_prompt: str | None = None,
        negative_prompt: str | None = None,
        height_cm: float | None = None,
        proportion_notes: str | None = None,
        voice_profile_id: str | None = None,
        multi_view_asset_ids: list[str] | None = None,
        shot_size_asset_ids: list[str] | None = None,
        expression_asset_ids: list[str] | None = None,
        outfit_asset_ids: list[str] | None = None,
        reference_priority: list[str] | None = None,
        notes: str | None = None,
        clear_voice: bool = False,
    ) -> dict[str, Any]:
        rev = self.get_revision(revision_id)
        if rev["status"] not in EDITABLE:
            raise ValueError(f"revision not editable: {rev['status']}")
        if height_cm is not None and (
            isinstance(height_cm, bool) or not isinstance(height_cm, (int, float))
        ):
            raise ValueError("height_cm must be a number")

        self._db.execute(
            """
            UPDATE character_identity_pack_revisions
            SET positive_prompt = ?, negative_prompt = ?, height_cm = ?,
                proportion_notes = ?, voice_profile_id = ?,
                multi_view_asset_ids_json = ?, shot_size_asset_ids_json = ?,
                expression_asset_ids_json = ?, outfit_asset_ids_json = ?,
                reference_priority_json = ?, notes = ?,
                status = 'draft', content_hash = NULL
            WHERE id = ?
            """,
            (
                positive_prompt.strip()
                if isinstance(positive_prompt, str)
                else rev["positive_prompt"],
                negative_prompt.strip()
                if isinstance(negative_prompt, str)
                else rev["negative_prompt"],
                float(height_cm) if height_cm is not None else rev["height_cm"],
                proportion_notes
                if proportion_notes is not None
                else rev["proportion_notes"],
                None
                if clear_voice
                else (
                    voice_profile_id
                    if voice_profile_id is not None
                    else rev["voice_profile_id"]
                ),
                _stable_json(
                    _as_str_list(multi_view_asset_ids, "multi_view_asset_ids")
                    if multi_view_asset_ids is not None
                    else rev["multi_view_asset_ids"]
                ),
                _stable_json(
                    _as_str_list(shot_size_asset_ids, "shot_size_asset_ids")
                    if shot_size_asset_ids is not None
                    else rev["shot_size_asset_ids"]
                ),
                _stable_json(
                    _as_str_list(expression_asset_ids, "expression_asset_ids")
                    if expression_asset_ids is not None
                    else rev["expression_asset_ids"]
                ),
                _stable_json(
                    _as_str_list(outfit_asset_ids, "outfit_asset_ids")
                    if outfit_asset_ids is not None
                    else rev["outfit_asset_ids"]
                ),
                _stable_json(
                    _as_str_list(reference_priority, "reference_priority")
                    if reference_priority is not None
                    else rev["reference_priority"]
                ),
                notes if notes is not None else rev["notes"],
                revision_id,
            ),
        )
        self._db.commit()
        return self.get_revision(revision_id)

    # --- look candidates ---

    def generate_looks(
        self,
        revision_id: str,
        *,
        count: int = 3,
        prompt_override: str | None = None,
    ) -> dict[str, Any]:
        rev = self.get_revision(revision_id)
        if rev["status"] not in EDITABLE | {"approved"}:
            if rev["status"] == "confirmed":
                raise ValueError("cannot generate looks for confirmed pack")
            if rev["status"] == "superseded":
                raise ValueError("cannot generate looks for superseded pack")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 4:
            raise ValueError("count must be an integer between 1 and 4")
        prompt = (prompt_override or rev["positive_prompt"] or "").strip()
        if not prompt:
            raise ValueError("positive_prompt is required to generate looks")
        negative = rev["negative_prompt"] or ""

        pack = self.get_pack(rev["pack_id"])
        character_id = pack["character_id"]
        created: list[dict[str, Any]] = []
        now = utc_now()

        row = self._db.fetchone(
            """
            SELECT COALESCE(MAX(candidate_no), 0) AS m
            FROM look_candidates WHERE identity_pack_revision_id = ?
            """,
            (revision_id,),
        )
        start_no = int(row["m"]) + 1 if row else 1

        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            for offset in range(count):
                candidate_no = start_no + offset
                candidate_id = str(uuid.uuid4())
                rel = (
                    f"assets/images/looks/{character_id}/"
                    f"{revision_id[:8]}_c{candidate_no}.jpg"
                )
                variant_prompt = f"{prompt} (look candidate {candidate_no})"
                generated = self._look_generator(
                    self._root, rel, variant_prompt, negative
                )
                self._db.execute(
                    """
                    INSERT INTO look_candidates(
                        id, identity_pack_revision_id, candidate_no, status,
                        prompt, negative_prompt, asset_rel_path, source,
                        provider_meta_json, width, height, created_at
                    ) VALUES (?, ?, ?, 'generated', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        revision_id,
                        candidate_no,
                        variant_prompt,
                        negative,
                        generated.get("asset_rel_path", rel),
                        generated.get("source", "mock"),
                        _stable_json(generated.get("provider_meta") or {}),
                        generated.get("width"),
                        generated.get("height"),
                        now,
                    ),
                )
                # Soft-attach generated asset into multi-view list for draft packs.
                created.append(self.get_candidate(candidate_id))
            # Mark draft dirty if was validated/approved.
            if rev["status"] in {"validated", "approved"}:
                self._db.execute(
                    """
                    UPDATE character_identity_pack_revisions
                    SET status = 'draft', content_hash = NULL
                    WHERE id = ?
                    """,
                    (revision_id,),
                )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise

        # Attach first generated paths into multi_view if empty.
        refreshed = self.get_revision(revision_id)
        if not refreshed["multi_view_asset_ids"] and created:
            paths = [c["asset_rel_path"] for c in created if c.get("asset_rel_path")]
            if paths:
                self.update_revision(revision_id, multi_view_asset_ids=paths[:3])
                refreshed = self.get_revision(revision_id)

        return {
            "revision": refreshed,
            "candidates": created,
            "count": len(created),
        }

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM look_candidates WHERE id = ?", (candidate_id,)
        )
        if row is None:
            raise ValueError(f"look candidate not found: {candidate_id}")
        return {
            "id": row["id"],
            "identity_pack_revision_id": row["identity_pack_revision_id"],
            "candidate_no": int(row["candidate_no"]),
            "status": row["status"],
            "prompt": row["prompt"],
            "negative_prompt": row["negative_prompt"],
            "asset_rel_path": row["asset_rel_path"],
            "source": row["source"],
            "provider_meta": json.loads(row["provider_meta_json"]),
            "width": row["width"],
            "height": row["height"],
            "created_at": row["created_at"],
        }

    def list_candidates(self, revision_id: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM look_candidates
            WHERE identity_pack_revision_id = ?
            ORDER BY candidate_no ASC
            """,
            (revision_id,),
        )
        return [self.get_candidate(row["id"]) for row in rows]

    def select_look(self, candidate_id: str) -> dict[str, Any]:
        cand = self.get_candidate(candidate_id)
        rev = self.get_revision(cand["identity_pack_revision_id"])
        if rev["status"] not in EDITABLE | {"approved"}:
            raise ValueError(f"cannot select look in status: {rev['status']}")
        if not cand.get("asset_rel_path"):
            raise ValueError("candidate has no asset path")
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE look_candidates
                SET status = 'rejected'
                WHERE identity_pack_revision_id = ?
                  AND status = 'selected'
                  AND id != ?
                """,
                (rev["id"], candidate_id),
            )
            self._db.execute(
                """
                UPDATE look_candidates SET status = 'selected' WHERE id = ?
                """,
                (candidate_id,),
            )
            self._db.execute(
                """
                UPDATE character_identity_pack_revisions
                SET selected_candidate_id = ?, status = 'draft',
                    content_hash = NULL
                WHERE id = ?
                """,
                (candidate_id, rev["id"]),
            )
            # Ensure selected asset is first multi-view ref.
            multi = rev["multi_view_asset_ids"]
            path = cand["asset_rel_path"]
            if path not in multi:
                multi = [path, *multi]
            else:
                multi = [path, *[p for p in multi if p != path]]
            self._db.execute(
                """
                UPDATE character_identity_pack_revisions
                SET multi_view_asset_ids_json = ?
                WHERE id = ?
                """,
                (_stable_json(multi), rev["id"]),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return {
            "revision": self.get_revision(rev["id"]),
            "candidate": self.get_candidate(candidate_id),
            "selected_at": now,
        }

    # --- validate / approve / confirm / gate ---

    def validate(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_revision(revision_id)
        if rev["status"] in {"confirmed", "superseded"}:
            raise ValueError(f"cannot validate status: {rev['status']}")
        errors = self._validation_errors(rev)
        status = "validated" if not errors else "draft"
        content_hash = None
        if not errors:
            content_hash = _hash(
                {
                    "positive_prompt": rev["positive_prompt"],
                    "negative_prompt": rev["negative_prompt"],
                    "multi_view_asset_ids": rev["multi_view_asset_ids"],
                    "selected_candidate_id": rev["selected_candidate_id"],
                    "height_cm": rev["height_cm"],
                    "voice_profile_id": rev["voice_profile_id"],
                }
            )
        self._db.execute(
            """
            UPDATE character_identity_pack_revisions
            SET status = ?, content_hash = ?
            WHERE id = ?
            """,
            (status, content_hash, revision_id),
        )
        self._db.commit()
        result = self.get_revision(revision_id)
        result["validation_errors"] = errors
        result["valid"] = not errors
        return result

    def approve(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_revision(revision_id)
        if rev["status"] == "draft":
            validated = self.validate(revision_id)
            if not validated["valid"]:
                raise ValueError(
                    f"identity pack validation failed: {validated['validation_errors'][0]}"
                )
            rev = self.get_revision(revision_id)
        if rev["status"] != "validated":
            raise ValueError(f"cannot approve status: {rev['status']}")
        now = utc_now()
        pack_id = rev["pack_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE character_identity_pack_revisions
                SET status = 'superseded'
                WHERE pack_id = ? AND status IN ('approved', 'confirmed')
                  AND id != ?
                """,
                (pack_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE character_identity_pack_revisions
                SET status = 'approved' WHERE id = ?
                """,
                (revision_id,),
            )
            self._db.execute(
                """
                UPDATE character_identity_packs
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

    def confirm(self, revision_id: str) -> dict[str, Any]:
        """Confirm identity pack for production (look lock)."""

        rev = self.get_revision(revision_id)
        if rev["status"] == "draft":
            self.approve(revision_id)
            rev = self.get_revision(revision_id)
        if rev["status"] not in {"validated", "approved"}:
            if rev["status"] == "confirmed":
                return self.get_pack(rev["pack_id"])
            raise ValueError(f"cannot confirm status: {rev['status']}")
        if rev["status"] == "validated":
            self.approve(revision_id)
            rev = self.get_revision(revision_id)

        errors = self._validation_errors(rev, require_selected=True)
        if errors:
            raise ValueError(f"identity pack confirmation failed: {errors[0]}")

        now = utc_now()
        pack_id = rev["pack_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE character_identity_pack_revisions
                SET status = 'superseded'
                WHERE pack_id = ? AND status = 'confirmed' AND id != ?
                """,
                (pack_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE character_identity_pack_revisions
                SET status = 'confirmed', confirmed_at = ?
                WHERE id = ?
                """,
                (now, revision_id),
            )
            self._db.execute(
                """
                UPDATE character_identity_packs
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

    def production_gate(self, character_id: str) -> dict[str, Any]:
        """Whether this character may enter bulk media generation."""

        character = self._characters.get_character(character_id)
        packs = self.list_packs(character_id=character_id, limit=20)
        confirmed = [
            p
            for p in packs
            if p.get("confirmed_revision_id")
            and p.get("confirmed_revision")
            and p["confirmed_revision"]["status"] == "confirmed"
        ]
        role = None
        if character.get("current_revision"):
            role = character["current_revision"].get("role")
        required = role in {"protagonist", "antagonist"}
        ready = bool(confirmed) if required else True
        return {
            "character_id": character_id,
            "role": role,
            "identity_pack_required": required,
            "ready_for_production": ready,
            "confirmed_pack_id": confirmed[0]["id"] if confirmed else None,
            "confirmed_revision_id": (
                confirmed[0]["confirmed_revision_id"] if confirmed else None
            ),
            "blocker": (
                None
                if ready
                else "main character identity pack is not confirmed"
            ),
        }

    def _validation_errors(
        self, rev: dict[str, Any], *, require_selected: bool = False
    ) -> list[str]:
        errors: list[str] = []
        if not (rev["positive_prompt"] or "").strip():
            errors.append("positive_prompt is required")
        candidates = rev.get("look_candidates") or self.list_candidates(rev["id"])
        if not candidates:
            errors.append("at least one look candidate is required")
        if require_selected or rev.get("selected_candidate_id"):
            if not rev.get("selected_candidate_id"):
                errors.append("selected_candidate_id is required")
            else:
                selected = next(
                    (
                        c
                        for c in candidates
                        if c["id"] == rev["selected_candidate_id"]
                    ),
                    None,
                )
                if selected is None:
                    errors.append("selected candidate not found")
                elif not selected.get("asset_rel_path"):
                    errors.append("selected candidate missing asset_rel_path")
        if not rev.get("multi_view_asset_ids") and not candidates:
            errors.append("multi_view_asset_ids or look candidates required")
        return errors

    def _default_look_generator(
        self,
        project_root: Path,
        rel_path: str,
        prompt: str,
        negative_prompt: str | None,
    ) -> dict[str, Any]:
        # Default: use Grok when on PATH unless explicitly disabled.
        disable = os.environ.get("WORKFLOW_DISABLE_GROK_LOOKS", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        force = os.environ.get("WORKFLOW_ENABLE_GROK_LOOKS", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        use_grok = (force or not disable) and bool(shutil.which("grok"))
        # In pytest, skip real grok unless forced (avoid auth/cost in CI)
        if os.environ.get("PYTEST_CURRENT_TEST") and not force:
            use_grok = False
        if use_grok and shutil.which("grok"):
            try:
                return self._generate_with_grok(
                    project_root, rel_path, prompt, negative_prompt
                )
            except Exception as exc:
                # Fall back to mock so offline/dev workflows continue.
                meta = {"grok_error": str(exc)[:500], "fallback": "mock"}
                return self._write_mock(project_root, rel_path, meta)
        return self._write_mock(
            project_root,
            rel_path,
            {"prompt": prompt, "negative_prompt": negative_prompt or ""},
        )

    def _write_mock(
        self, project_root: Path, rel_path: str, meta: dict[str, Any]
    ) -> dict[str, Any]:
        dest = project_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_MOCK_JPEG)
        return {
            "asset_rel_path": rel_path,
            "source": "mock",
            "width": 1,
            "height": 1,
            "provider_meta": meta,
        }

    def _generate_with_grok(
        self,
        project_root: Path,
        rel_path: str,
        prompt: str,
        negative_prompt: str | None,
    ) -> dict[str, Any]:
        """Invoke grok image_gen CLI (M0-05 contract) and copy into project assets."""

        schema = {
            "type": "object",
            "required": ["image_path", "ok"],
            "properties": {
                "image_path": {"type": "string"},
                "ok": {"type": "boolean"},
            },
        }
        neg = f" Avoid: {negative_prompt}." if negative_prompt else ""
        full_prompt = (
            "Use image_gen to create one character look reference image. "
            f"Subject: {prompt}.{neg} "
            "Report the absolute image path as image_path and ok=true."
        )
        command = [
            "grok",
            "-p",
            full_prompt,
            "--yolo",
            "--tools",
            "image_gen",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            "--max-turns",
            "3",
        ]
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=180,
            env=os.environ.copy(),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"grok exit {completed.returncode}: "
                f"{(completed.stderr or completed.stdout)[-400:]}"
            )
        payload = json.loads(completed.stdout)
        structured = payload.get("structuredOutput") or payload
        if isinstance(structured, str):
            structured = json.loads(structured)
        image_path = structured.get("image_path")
        if not image_path or not Path(image_path).is_file():
            raise RuntimeError("grok did not return a readable image_path")
        dest = project_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, dest)
        width, height = None, None
        try:
            from PIL import Image

            with Image.open(dest) as image:
                width, height = image.size
        except Exception:
            pass
        return {
            "asset_rel_path": rel_path,
            "source": "grok_image_gen",
            "width": width,
            "height": height,
            "provider_meta": {
                "provider": "grok",
                "tool": "image_gen",
                "session_path": image_path,
            },
        }
