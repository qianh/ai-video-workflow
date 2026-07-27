"""Storyboards and shots with continuity snapshots (M3-10)."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .continuity import ContinuityService
from .database import Database
from .timeutil import utc_now

SHOT_TYPES = frozenset(
    {"establishing", "dialogue", "reaction", "action", "environment", "insert", "other"}
)
FRAMINGS = frozenset({"ECU", "CU", "MCU", "MS", "MLS", "WS", "EWS"})
GEN_MODES = frozenset({"static_motion", "image_to_video", "reuse"})
LIP_LEVELS = frozenset({"precise", "simplified", "none"})


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


class StoryboardService:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._continuity = ContinuityService(db)

    def create_storyboard(
        self,
        *,
        episode_id: str,
        branch_id: str,
        script_revision_id: str | None = None,
        director_preset_revision_id: str | None = None,
        visual_bible_revision_id: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        sb_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO storyboards(
                    id, episode_id, branch_id, status, current_revision_id,
                    confirmed_revision_id, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', NULL, NULL, ?, ?)
                """,
                (sb_id, episode_id, branch_id, now, now),
            )
            self._db.execute(
                """
                INSERT INTO storyboard_revisions(
                    id, storyboard_id, revision_no, status, script_revision_id,
                    director_preset_revision_id, visual_bible_revision_id,
                    estimated_duration_ms, content_hash, notes, created_at, confirmed_at
                ) VALUES (?, ?, 1, 'draft', ?, ?, ?, 0, NULL, ?, ?, NULL)
                """,
                (
                    rev_id,
                    sb_id,
                    script_revision_id,
                    director_preset_revision_id,
                    visual_bible_revision_id,
                    notes,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_storyboard(sb_id)

    def get_storyboard(self, storyboard_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM storyboards WHERE id = ?", (storyboard_id,)
        )
        if row is None:
            raise ValueError(f"storyboard not found: {storyboard_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_revision(row["current_revision_id"])
        else:
            latest = self._db.fetchone(
                """
                SELECT id FROM storyboard_revisions
                WHERE storyboard_id = ? ORDER BY revision_no DESC LIMIT 1
                """,
                (storyboard_id,),
            )
            if latest:
                current = self.get_revision(latest["id"])
        confirmed = None
        if row["confirmed_revision_id"]:
            confirmed = self.get_revision(row["confirmed_revision_id"])
        return {
            "id": row["id"],
            "episode_id": row["episode_id"],
            "branch_id": row["branch_id"],
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "confirmed_revision_id": row["confirmed_revision_id"],
            "current_revision": current,
            "confirmed_revision": confirmed,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM storyboard_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"storyboard revision not found: {revision_id}")
        shots = self.list_shots(revision_id)
        total = sum(int(s["current_revision"]["duration_ms"]) for s in shots if s.get("current_revision"))
        return {
            "id": row["id"],
            "storyboard_id": row["storyboard_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "script_revision_id": row["script_revision_id"],
            "director_preset_revision_id": row["director_preset_revision_id"],
            "visual_bible_revision_id": row["visual_bible_revision_id"],
            "estimated_duration_ms": total or row["estimated_duration_ms"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "confirmed_at": row["confirmed_at"],
            "shots": shots,
            "shot_count": len(shots),
        }

    def add_shot(
        self,
        *,
        storyboard_revision_id: str,
        shot_type: str = "dialogue",
        framing: str = "MS",
        camera_angle: str = "eye_level",
        duration_ms: int = 2000,
        purpose: str | None = None,
        action_text: str | None = None,
        scene_revision_id: str | None = None,
        location_revision_id: str | None = None,
        character_revision_ids: list[str] | None = None,
        dialogue_line_revision_ids: list[str] | None = None,
        generation_mode: str = "static_motion",
        lip_sync_level: str = "none",
        camera_motion: dict[str, Any] | None = None,
        continuity_at_ord: int | None = None,
        branch_id: str | None = None,
        shot_no: int | None = None,
    ) -> dict[str, Any]:
        rev = self.get_revision(storyboard_revision_id)
        if rev["status"] not in {"draft", "validated"}:
            raise ValueError(f"cannot add shot in status: {rev['status']}")
        if shot_type not in SHOT_TYPES:
            raise ValueError(f"shot_type must be one of {sorted(SHOT_TYPES)}")
        if framing not in FRAMINGS:
            raise ValueError(f"framing must be one of {sorted(FRAMINGS)}")
        if generation_mode not in GEN_MODES:
            raise ValueError(f"generation_mode must be one of {sorted(GEN_MODES)}")
        if lip_sync_level not in LIP_LEVELS:
            raise ValueError(f"lip_sync_level must be one of {sorted(LIP_LEVELS)}")
        if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 1:
            raise ValueError("duration_ms must be a positive integer")

        if shot_no is None:
            row = self._db.fetchone(
                """
                SELECT COALESCE(MAX(shot_no), 0) AS m FROM shots
                WHERE storyboard_revision_id = ?
                """,
                (storyboard_revision_id,),
            )
            shot_no = int(row["m"]) + 1

        snap_id = None
        if continuity_at_ord is not None and branch_id:
            try:
                snap = self._continuity.create_snapshot(
                    branch_id=branch_id,
                    at_story_time=f"shot-{shot_no}",
                    at_time_ord=continuity_at_ord,
                    purpose=f"shot {shot_no}",
                )
                snap_id = snap["id"]
            except ValueError:
                snap_id = None

        shot_id = str(uuid.uuid4())
        srev_id = str(uuid.uuid4())
        now = utc_now()
        payload = {
            "shot_type": shot_type,
            "framing": framing,
            "camera_angle": camera_angle,
            "duration_ms": duration_ms,
            "purpose": purpose,
            "action_text": action_text,
            "generation_mode": generation_mode,
            "lip_sync_level": lip_sync_level,
        }
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO shots(
                    id, storyboard_revision_id, shot_no, status,
                    current_revision_id, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', NULL, ?, ?)
                """,
                (shot_id, storyboard_revision_id, shot_no, now, now),
            )
            self._db.execute(
                """
                INSERT INTO shot_revisions(
                    id, shot_id, revision_no, status, scene_revision_id, shot_type,
                    framing, camera_angle, camera_motion_json, duration_ms,
                    character_revision_ids_json, location_revision_id,
                    continuity_snapshot_id, dialogue_line_revision_ids_json,
                    generation_mode, lip_sync_level, purpose, action_text,
                    content_hash, created_at
                ) VALUES (?, ?, 1, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    srev_id,
                    shot_id,
                    scene_revision_id,
                    shot_type,
                    framing,
                    camera_angle,
                    _stable_json(camera_motion or {}),
                    duration_ms,
                    _stable_json(character_revision_ids or []),
                    location_revision_id,
                    snap_id,
                    _stable_json(dialogue_line_revision_ids or []),
                    generation_mode,
                    lip_sync_level,
                    purpose,
                    action_text,
                    _hash(payload),
                    now,
                ),
            )
            self._db.execute(
                "UPDATE shots SET current_revision_id = ?, updated_at = ? WHERE id = ?",
                (srev_id, now, shot_id),
            )
            self._db.execute(
                """
                UPDATE storyboard_revisions
                SET status = 'draft', content_hash = NULL
                WHERE id = ?
                """,
                (storyboard_revision_id,),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_shot(shot_id)

    def generate_default_shots(
        self,
        storyboard_revision_id: str,
        *,
        count: int = 24,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(count, int) or isinstance(count, bool) or count < 6 or count > 30:
            raise ValueError("count must be between 6 and 30")
        created = []
        pattern = [
            ("establishing", "WS", 2500, "none"),
            ("dialogue", "MS", 2000, "simplified"),
            ("reaction", "CU", 1500, "none"),
            ("action", "MS", 2200, "none"),
            ("environment", "WS", 1800, "none"),
            ("dialogue", "MCU", 2100, "precise"),
        ]
        for i in range(count):
            shot_type, framing, duration, lip = pattern[i % len(pattern)]
            created.append(
                self.add_shot(
                    storyboard_revision_id=storyboard_revision_id,
                    shot_type=shot_type,
                    framing=framing,
                    duration_ms=duration,
                    lip_sync_level=lip,
                    purpose=f"auto shot {i + 1}",
                    action_text=f"beat {i + 1}",
                    generation_mode="static_motion",
                    branch_id=branch_id,
                    continuity_at_ord=100 + i * 10 if branch_id else None,
                )
            )
        return {
            "storyboard_revision_id": storyboard_revision_id,
            "shots": created,
            "count": len(created),
        }

    def get_shot(self, shot_id: str) -> dict[str, Any]:
        row = self._db.fetchone("SELECT * FROM shots WHERE id = ?", (shot_id,))
        if row is None:
            raise ValueError(f"shot not found: {shot_id}")
        current = None
        if row["current_revision_id"]:
            current = self.get_shot_revision(row["current_revision_id"])
        return {
            "id": row["id"],
            "storyboard_revision_id": row["storyboard_revision_id"],
            "shot_no": int(row["shot_no"]),
            "status": row["status"],
            "current_revision_id": row["current_revision_id"],
            "current_revision": current,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_shot_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM shot_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"shot revision not found: {revision_id}")
        return {
            "id": row["id"],
            "shot_id": row["shot_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "scene_revision_id": row["scene_revision_id"],
            "shot_type": row["shot_type"],
            "framing": row["framing"],
            "camera_angle": row["camera_angle"],
            "camera_motion": json.loads(row["camera_motion_json"]),
            "duration_ms": int(row["duration_ms"]),
            "character_revision_ids": json.loads(row["character_revision_ids_json"]),
            "location_revision_id": row["location_revision_id"],
            "continuity_snapshot_id": row["continuity_snapshot_id"],
            "dialogue_line_revision_ids": json.loads(
                row["dialogue_line_revision_ids_json"]
            ),
            "generation_mode": row["generation_mode"],
            "lip_sync_level": row["lip_sync_level"],
            "purpose": row["purpose"],
            "action_text": row["action_text"],
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
        }

    def list_shots(self, storyboard_revision_id: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM shots
            WHERE storyboard_revision_id = ? AND status = 'active'
            ORDER BY shot_no ASC
            """,
            (storyboard_revision_id,),
        )
        return [self.get_shot(row["id"]) for row in rows]

    def approve_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_revision(revision_id)
        if rev["shot_count"] < 6:
            raise ValueError("storyboard needs at least 6 shots")
        if rev["shot_count"] > 30:
            raise ValueError("storyboard cannot exceed 30 shots")
        content_hash = _hash(
            {
                "shots": [
                    s["current_revision"]["content_hash"]
                    for s in rev["shots"]
                    if s.get("current_revision")
                ]
            }
        )
        now = utc_now()
        self._db.execute(
            """
            UPDATE storyboard_revisions
            SET status = 'approved', content_hash = ?, estimated_duration_ms = ?
            WHERE id = ?
            """,
            (content_hash, rev["estimated_duration_ms"], revision_id),
        )
        self._db.execute(
            """
            UPDATE storyboards
            SET current_revision_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (revision_id, now, rev["storyboard_id"]),
        )
        self._db.commit()
        return self.get_storyboard(rev["storyboard_id"])

    def confirm_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_revision(revision_id)
        if rev["status"] == "draft":
            self.approve_revision(revision_id)
            rev = self.get_revision(revision_id)
        if rev["status"] not in {"approved", "confirmed"}:
            raise ValueError(f"cannot confirm status: {rev['status']}")
        now = utc_now()
        self._db.execute(
            """
            UPDATE storyboard_revisions
            SET status = 'confirmed', confirmed_at = ?
            WHERE id = ?
            """,
            (now, revision_id),
        )
        self._db.execute(
            """
            UPDATE storyboards
            SET current_revision_id = ?, confirmed_revision_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (revision_id, revision_id, now, rev["storyboard_id"]),
        )
        self._db.commit()
        return self.get_storyboard(rev["storyboard_id"])

    def list_storyboards(self, *, episode_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if episode_id:
            rows = self._db.fetchall(
                """
                SELECT id FROM storyboards WHERE episode_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (episode_id, limit),
            )
        else:
            rows = self._db.fetchall(
                "SELECT id FROM storyboards ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [self.get_storyboard(row["id"]) for row in rows]
