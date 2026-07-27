"""Characters, relationships, and voice profiles (M2-09).

Characters are stable identities; narrative attributes live in revisions.
Relationships are directed and branch-scoped. Voice profiles may attach to a
character or stand alone for narration. Identity packs (M2-10) are separate.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import Database
from .timeutil import utc_now

ENTITY_STATUSES = frozenset({"active", "archived"})
REV_STATUSES = frozenset({"draft", "validated", "approved", "superseded"})
EDITABLE = frozenset({"draft", "validated"})
CHAR_ROLES = frozenset(
    {"protagonist", "antagonist", "supporting", "extra", "narrator", "other"}
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


class CharacterService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- characters ---

    def create_character(
        self,
        *,
        branch_id: str,
        name: str,
        role: str = "supporting",
        age_feel: str | None = None,
        body_type: str | None = None,
        appearance_rules: str | None = None,
        personality: list[str] | None = None,
        goals: str | None = None,
        immutable_traits: list[str] | None = None,
        slug: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        if role not in CHAR_ROLES:
            raise ValueError(f"role must be one of {sorted(CHAR_ROLES)}")
        personality = _as_str_list(personality, "personality")
        immutable_traits = _as_str_list(immutable_traits, "immutable_traits")
        character_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO characters(
                    id, branch_id, slug, status, current_revision_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'active', NULL, ?, ?)
                """,
                (
                    character_id,
                    branch_id,
                    slug.strip() if isinstance(slug, str) and slug.strip() else None,
                    now,
                    now,
                ),
            )
            self._db.execute(
                """
                INSERT INTO character_revisions(
                    id, character_id, revision_no, status, name, role,
                    age_feel, body_type, appearance_rules, personality_json,
                    goals, immutable_traits_json, content_hash, notes, created_at
                ) VALUES (?, ?, 1, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    revision_id,
                    character_id,
                    name,
                    role,
                    age_feel,
                    body_type,
                    appearance_rules,
                    _stable_json(personality),
                    goals,
                    _stable_json(immutable_traits),
                    notes,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_character(character_id)

    def get_character(self, character_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM characters WHERE id = ?", (character_id,)
        )
        if row is None:
            raise ValueError(f"character not found: {character_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_character_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM character_revisions
                WHERE character_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (character_id,),
            )
            if latest:
                current = self.get_character_revision(latest["id"])
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "slug": row["slug"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "current_revision": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_characters(
        self, *, branch_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if branch_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM characters
                WHERE branch_id = ? AND status = 'active'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (branch_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM characters
                WHERE status = 'active'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            )
        return [self.get_character(row["id"]) for row in rows]

    def get_character_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM character_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"character revision not found: {revision_id}")
        return {
            "id": row["id"],
            "character_id": row["character_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "name": row["name"],
            "role": row["role"],
            "age_feel": row["age_feel"],
            "body_type": row["body_type"],
            "appearance_rules": row["appearance_rules"],
            "personality": json.loads(row["personality_json"]),
            "goals": row["goals"],
            "immutable_traits": json.loads(row["immutable_traits_json"]),
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "contains_media_prompts": False,
        }

    def update_character_revision(
        self,
        revision_id: str,
        *,
        name: str | None = None,
        role: str | None = None,
        age_feel: str | None = None,
        body_type: str | None = None,
        appearance_rules: str | None = None,
        personality: list[str] | None = None,
        goals: str | None = None,
        immutable_traits: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        rev = self.get_character_revision(revision_id)
        if rev["status"] not in EDITABLE:
            raise ValueError(f"revision not editable: {rev['status']}")
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("name is required")
        if role is not None and role not in CHAR_ROLES:
            raise ValueError(f"role must be one of {sorted(CHAR_ROLES)}")
        personality_json = (
            _stable_json(_as_str_list(personality, "personality"))
            if personality is not None
            else _stable_json(rev["personality"])
        )
        traits_json = (
            _stable_json(_as_str_list(immutable_traits, "immutable_traits"))
            if immutable_traits is not None
            else _stable_json(rev["immutable_traits"])
        )
        self._db.execute(
            """
            UPDATE character_revisions
            SET name = ?, role = ?, age_feel = ?, body_type = ?,
                appearance_rules = ?, personality_json = ?, goals = ?,
                immutable_traits_json = ?, notes = ?, status = 'draft',
                content_hash = NULL
            WHERE id = ?
            """,
            (
                name if name is not None else rev["name"],
                role if role is not None else rev["role"],
                age_feel if age_feel is not None else rev["age_feel"],
                body_type if body_type is not None else rev["body_type"],
                appearance_rules
                if appearance_rules is not None
                else rev["appearance_rules"],
                personality_json,
                goals if goals is not None else rev["goals"],
                traits_json,
                notes if notes is not None else rev["notes"],
                revision_id,
            ),
        )
        self._db.commit()
        return self.get_character_revision(revision_id)

    def validate_character_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_character_revision(revision_id)
        if rev["status"] in {"approved", "superseded"}:
            raise ValueError(f"cannot validate status: {rev['status']}")
        errors = self._character_errors(rev)
        now_status = "validated" if not errors else "draft"
        content_hash = None
        if not errors:
            content_hash = _hash(
                {
                    "name": rev["name"],
                    "role": rev["role"],
                    "age_feel": rev["age_feel"],
                    "body_type": rev["body_type"],
                    "appearance_rules": rev["appearance_rules"],
                    "personality": rev["personality"],
                    "goals": rev["goals"],
                    "immutable_traits": rev["immutable_traits"],
                }
            )
        self._db.execute(
            """
            UPDATE character_revisions
            SET status = ?, content_hash = ?
            WHERE id = ?
            """,
            (now_status, content_hash, revision_id),
        )
        self._db.commit()
        result = self.get_character_revision(revision_id)
        result["validation_errors"] = errors
        result["valid"] = not errors
        return result

    def approve_character_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_character_revision(revision_id)
        if rev["status"] == "draft":
            validated = self.validate_character_revision(revision_id)
            if not validated["valid"]:
                raise ValueError(
                    f"character validation failed: {validated['validation_errors'][0]}"
                )
            rev = self.get_character_revision(revision_id)
        if rev["status"] != "validated":
            raise ValueError(f"cannot approve status: {rev['status']}")
        now = utc_now()
        character_id = rev["character_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE character_revisions
                SET status = 'superseded'
                WHERE character_id = ? AND status = 'approved' AND id != ?
                """,
                (character_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE character_revisions SET status = 'approved' WHERE id = ?
                """,
                (revision_id,),
            )
            self._db.execute(
                """
                UPDATE characters
                SET current_revision_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (revision_id, now, character_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_character(character_id)

    def _character_errors(self, rev: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not (rev["name"] or "").strip():
            errors.append("name is required")
        if not rev["role"]:
            errors.append("role is required")
        if not rev["personality"]:
            errors.append("personality requires at least one trait")
        if not (rev["appearance_rules"] or "").strip():
            errors.append("appearance_rules is required")
        return errors

    # --- relationships ---

    def create_relationship(
        self,
        *,
        branch_id: str,
        source_character_id: str,
        target_character_id: str,
        relationship_type: str,
        description: str,
        story_time_from: str | None = None,
        story_time_to: str | None = None,
    ) -> dict[str, Any]:
        if source_character_id == target_character_id:
            raise ValueError("source and target must differ")
        self.get_character(source_character_id)
        self.get_character(target_character_id)
        relationship_type = relationship_type.strip()
        description = description.strip()
        if not relationship_type or not description:
            raise ValueError("relationship_type and description are required")
        rel_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO character_relationships(
                    id, branch_id, source_character_id, target_character_id,
                    status, current_revision_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)
                """,
                (
                    rel_id,
                    branch_id,
                    source_character_id,
                    target_character_id,
                    now,
                    now,
                ),
            )
            self._db.execute(
                """
                INSERT INTO character_relationship_revisions(
                    id, relationship_id, revision_no, relationship_type,
                    description, story_time_from, story_time_to, status, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, 'draft', ?)
                """,
                (
                    rev_id,
                    rel_id,
                    relationship_type,
                    description,
                    story_time_from,
                    story_time_to,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_relationship(rel_id)

    def get_relationship(self, relationship_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM character_relationships WHERE id = ?",
            (relationship_id,),
        )
        if row is None:
            raise ValueError(f"relationship not found: {relationship_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_relationship_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM character_relationship_revisions
                WHERE relationship_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (relationship_id,),
            )
            if latest:
                current = self.get_relationship_revision(latest["id"])
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "source_character_id": row["source_character_id"],
            "target_character_id": row["target_character_id"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "current_revision": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_relationship_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM character_relationship_revisions WHERE id = ?",
            (revision_id,),
        )
        if row is None:
            raise ValueError(f"relationship revision not found: {revision_id}")
        return {
            "id": row["id"],
            "relationship_id": row["relationship_id"],
            "revision_no": int(row["revision_no"]),
            "relationship_type": row["relationship_type"],
            "description": row["description"],
            "story_time_from": row["story_time_from"],
            "story_time_to": row["story_time_to"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def list_relationships(
        self, *, branch_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if branch_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM character_relationships
                WHERE branch_id = ? AND status = 'active'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (branch_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM character_relationships
                WHERE status = 'active'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            )
        return [self.get_relationship(row["id"]) for row in rows]

    def approve_relationship_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_relationship_revision(revision_id)
        if rev["status"] not in {"draft", "validated"}:
            raise ValueError(f"cannot approve status: {rev['status']}")
        if not rev["relationship_type"].strip() or not rev["description"].strip():
            raise ValueError("relationship_type and description are required")
        now = utc_now()
        rel_id = rev["relationship_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE character_relationship_revisions
                SET status = 'superseded'
                WHERE relationship_id = ? AND status = 'approved' AND id != ?
                """,
                (rel_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE character_relationship_revisions
                SET status = 'approved' WHERE id = ?
                """,
                (revision_id,),
            )
            self._db.execute(
                """
                UPDATE character_relationships
                SET current_revision_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (revision_id, now, rel_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_relationship(rel_id)

    # --- voice profiles ---

    def create_voice_profile(
        self,
        *,
        character_id: str | None = None,
        label: str | None = None,
        engine_adapter_id: str = "local-tts",
        speed: float = 1.0,
        emotion_range: list[str] | None = None,
        pronunciation_rules: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if character_id is not None:
            self.get_character(character_id)
        engine_adapter_id = engine_adapter_id.strip()
        if not engine_adapter_id:
            raise ValueError("engine_adapter_id is required")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            raise ValueError("speed must be a number")
        if speed <= 0 or speed > 3:
            raise ValueError("speed must be between 0 exclusive and 3 inclusive")
        emotion_range = _as_str_list(emotion_range, "emotion_range")
        if pronunciation_rules is None:
            pronunciation_rules = {}
        if not isinstance(pronunciation_rules, dict):
            raise ValueError("pronunciation_rules must be an object")
        profile_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO voice_profiles(
                    id, character_id, label, status, current_revision_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'active', NULL, ?, ?)
                """,
                (
                    profile_id,
                    character_id,
                    label.strip() if isinstance(label, str) and label.strip() else None,
                    now,
                    now,
                ),
            )
            self._db.execute(
                """
                INSERT INTO voice_profile_revisions(
                    id, voice_profile_id, revision_no, status, engine_adapter_id,
                    voice_ref_asset_id, speaker_embedding_asset_id, speed,
                    emotion_range_json, pronunciation_rules_json,
                    authorization_record_id, content_hash, notes, created_at
                ) VALUES (?, ?, 1, 'draft', ?, NULL, NULL, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    rev_id,
                    profile_id,
                    engine_adapter_id,
                    float(speed),
                    _stable_json(emotion_range),
                    _stable_json(pronunciation_rules),
                    notes,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_voice_profile(profile_id)

    def get_voice_profile(self, voice_profile_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM voice_profiles WHERE id = ?", (voice_profile_id,)
        )
        if row is None:
            raise ValueError(f"voice profile not found: {voice_profile_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_voice_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM voice_profile_revisions
                WHERE voice_profile_id = ?
                ORDER BY revision_no DESC LIMIT 1
                """,
                (voice_profile_id,),
            )
            if latest:
                current = self.get_voice_revision(latest["id"])
        return {
            "id": row["id"],
            "character_id": row["character_id"],
            "label": row["label"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "current_revision": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_voice_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM voice_profile_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"voice revision not found: {revision_id}")
        return {
            "id": row["id"],
            "voice_profile_id": row["voice_profile_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "engine_adapter_id": row["engine_adapter_id"],
            "voice_ref_asset_id": row["voice_ref_asset_id"],
            "speaker_embedding_asset_id": row["speaker_embedding_asset_id"],
            "speed": float(row["speed"]),
            "emotion_range": json.loads(row["emotion_range_json"]),
            "pronunciation_rules": json.loads(row["pronunciation_rules_json"]),
            "authorization_record_id": row["authorization_record_id"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "contains_media_prompts": False,
        }

    def list_voice_profiles(
        self, *, character_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if character_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM voice_profiles
                WHERE character_id = ? AND status = 'active'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (character_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM voice_profiles
                WHERE status = 'active'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            )
        return [self.get_voice_profile(row["id"]) for row in rows]

    def approve_voice_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_voice_revision(revision_id)
        if rev["status"] not in {"draft", "validated"}:
            raise ValueError(f"cannot approve status: {rev['status']}")
        if not rev["engine_adapter_id"].strip():
            raise ValueError("engine_adapter_id is required")
        content_hash = _hash(
            {
                "engine_adapter_id": rev["engine_adapter_id"],
                "speed": rev["speed"],
                "emotion_range": rev["emotion_range"],
                "pronunciation_rules": rev["pronunciation_rules"],
            }
        )
        now = utc_now()
        profile_id = rev["voice_profile_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE voice_profile_revisions
                SET status = 'superseded'
                WHERE voice_profile_id = ? AND status = 'approved' AND id != ?
                """,
                (profile_id, revision_id),
            )
            self._db.execute(
                """
                UPDATE voice_profile_revisions
                SET status = 'approved', content_hash = ?
                WHERE id = ?
                """,
                (content_hash, revision_id),
            )
            self._db.execute(
                """
                UPDATE voice_profiles
                SET current_revision_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (revision_id, now, profile_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_voice_profile(profile_id)

    def continuity_overview(self, branch_id: str) -> dict[str, Any]:
        return {
            "branch_id": branch_id,
            "characters": self.list_characters(branch_id=branch_id),
            "relationships": self.list_relationships(branch_id=branch_id),
            "voice_profiles": [
                item
                for item in self.list_voice_profiles(limit=100)
                if item["character_id"] is None
                or any(
                    c["id"] == item["character_id"]
                    for c in self.list_characters(branch_id=branch_id, limit=100)
                )
            ],
        }
