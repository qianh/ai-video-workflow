"""Episode script, scene, dialogue, and hook editing (M2-08).

Episode is a stable container. Narrative content lives in script revisions.
Dialogue uses stable line_id + immutable revisions. Scripts never embed
media prompts or shot parameters.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import Database
from .timeutil import utc_now

SCRIPT_STATUSES = frozenset({"draft", "validated", "approved", "superseded"})
EDITABLE_SCRIPT_STATUSES = frozenset({"draft", "validated"})
LINE_TYPES = frozenset({"dialogue", "narration", "voiceover"})
HOOK_TYPES = frozenset({"opening", "mid", "ending", "cliffhanger"})
TIME_OF_DAY = frozenset(
    {"dawn", "day", "dusk", "night", "continuous", "unspecified"}
)


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


class EpisodeScriptService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- episode helpers ---

    def get_episode(self, episode_id: str) -> dict[str, Any]:
        row = self._db.fetchone("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        if row is None:
            raise ValueError(f"episode not found: {episode_id}")
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "episode_no": int(row["episode_no"]),
            "title": row["title"],
            "status": row["status"],
            "current_script_revision_id": row["current_script_revision_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_episode_title(self, episode_id: str, title: str) -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise ValueError("title must be a non-empty string")
        now = utc_now()
        self._db.execute(
            """
            UPDATE episodes SET title = ?, updated_at = ? WHERE id = ?
            """,
            (title, now, episode_id),
        )
        self._db.commit()
        return self.get_episode(episode_id)

    # --- script revisions ---

    def create_script(
        self,
        *,
        episode_id: str,
        title: str | None = None,
        goal: str = "",
        main_conflict: str = "",
        twist: str | None = None,
        opening_hook: str = "",
        ending_hook: str = "",
        estimated_duration_ms: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        episode = self.get_episode(episode_id)
        if estimated_duration_ms is not None:
            if (
                isinstance(estimated_duration_ms, bool)
                or not isinstance(estimated_duration_ms, int)
                or estimated_duration_ms < 0
            ):
                raise ValueError("estimated_duration_ms must be a non-negative integer")

        row = self._db.fetchone(
            """
            SELECT COALESCE(MAX(revision_no), 0) AS m
            FROM episode_script_revisions WHERE episode_id = ?
            """,
            (episode_id,),
        )
        revision_no = int(row["m"]) + 1 if row else 1
        script_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO episode_script_revisions(
                    id, episode_id, branch_id, revision_no, status, title,
                    goal, main_conflict, twist, opening_hook, ending_hook,
                    estimated_duration_ms, content_hash, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    script_id,
                    episode_id,
                    episode["branch_id"],
                    revision_no,
                    title.strip() if isinstance(title, str) and title.strip() else None,
                    goal.strip() if isinstance(goal, str) else "",
                    main_conflict.strip() if isinstance(main_conflict, str) else "",
                    twist.strip() if isinstance(twist, str) and twist.strip() else None,
                    opening_hook.strip() if isinstance(opening_hook, str) else "",
                    ending_hook.strip() if isinstance(ending_hook, str) else "",
                    estimated_duration_ms,
                    notes,
                    now,
                    now,
                ),
            )
            if episode["status"] == "planned":
                self._db.execute(
                    """
                    UPDATE episodes SET status = 'scripting', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, episode_id),
                )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_script(script_id)

    def update_script(
        self,
        script_id: str,
        *,
        title: str | None = None,
        goal: str | None = None,
        main_conflict: str | None = None,
        twist: str | None = None,
        opening_hook: str | None = None,
        ending_hook: str | None = None,
        estimated_duration_ms: int | None = None,
        notes: str | None = None,
        clear_twist: bool = False,
    ) -> dict[str, Any]:
        script = self.get_script(script_id)
        self._require_editable(script)
        if estimated_duration_ms is not None:
            if (
                isinstance(estimated_duration_ms, bool)
                or not isinstance(estimated_duration_ms, int)
                or estimated_duration_ms < 0
            ):
                raise ValueError("estimated_duration_ms must be a non-negative integer")

        fields: dict[str, Any] = {
            "title": script["title"],
            "goal": script["goal"],
            "main_conflict": script["main_conflict"],
            "twist": script["twist"],
            "opening_hook": script["opening_hook"],
            "ending_hook": script["ending_hook"],
            "estimated_duration_ms": script["estimated_duration_ms"],
            "notes": script["notes"],
        }
        if title is not None:
            fields["title"] = title.strip() or None
        if goal is not None:
            fields["goal"] = goal.strip()
        if main_conflict is not None:
            fields["main_conflict"] = main_conflict.strip()
        if clear_twist:
            fields["twist"] = None
        elif twist is not None:
            fields["twist"] = twist.strip() or None
        if opening_hook is not None:
            fields["opening_hook"] = opening_hook.strip()
        if ending_hook is not None:
            fields["ending_hook"] = ending_hook.strip()
        if estimated_duration_ms is not None:
            fields["estimated_duration_ms"] = estimated_duration_ms
        if notes is not None:
            fields["notes"] = notes

        now = utc_now()
        self._db.execute(
            """
            UPDATE episode_script_revisions
            SET title = ?, goal = ?, main_conflict = ?, twist = ?,
                opening_hook = ?, ending_hook = ?, estimated_duration_ms = ?,
                notes = ?, status = 'draft', content_hash = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                fields["title"],
                fields["goal"],
                fields["main_conflict"],
                fields["twist"],
                fields["opening_hook"],
                fields["ending_hook"],
                fields["estimated_duration_ms"],
                fields["notes"],
                now,
                script_id,
            ),
        )
        self._db.commit()
        return self.get_script(script_id)

    def get_script(self, script_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM episode_script_revisions WHERE id = ?", (script_id,)
        )
        if row is None:
            raise ValueError(f"script revision not found: {script_id}")
        return {
            "id": row["id"],
            "episode_id": row["episode_id"],
            "branch_id": row["branch_id"],
            "revision_no": int(row["revision_no"]),
            "status": row["status"],
            "title": row["title"],
            "goal": row["goal"],
            "main_conflict": row["main_conflict"],
            "twist": row["twist"],
            "opening_hook": row["opening_hook"],
            "ending_hook": row["ending_hook"],
            "estimated_duration_ms": row["estimated_duration_ms"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "contains_media_prompts": False,
        }

    def list_scripts(
        self, *, episode_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if episode_id is not None:
            rows = self._db.fetchall(
                """
                SELECT id FROM episode_script_revisions
                WHERE episode_id = ?
                ORDER BY revision_no DESC
                LIMIT ?
                """,
                (episode_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT id FROM episode_script_revisions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [self.get_script(row["id"]) for row in rows]

    # --- scenes ---

    def add_scene(
        self,
        *,
        script_revision_id: str,
        scene_no: int | None = None,
        purpose: str,
        action_text: str,
        time_of_day: str = "night",
        location_ref: str | None = None,
        story_time_start: str | None = None,
        estimated_duration_ms: int | None = None,
    ) -> dict[str, Any]:
        script = self.get_script(script_revision_id)
        self._require_editable(script)
        purpose = purpose.strip()
        action_text = action_text.strip()
        if not purpose or not action_text:
            raise ValueError("purpose and action_text are required")
        if time_of_day not in TIME_OF_DAY:
            raise ValueError(f"time_of_day must be one of {sorted(TIME_OF_DAY)}")
        if estimated_duration_ms is not None:
            if (
                isinstance(estimated_duration_ms, bool)
                or not isinstance(estimated_duration_ms, int)
                or estimated_duration_ms < 0
            ):
                raise ValueError("estimated_duration_ms must be a non-negative integer")

        if scene_no is None:
            row = self._db.fetchone(
                """
                SELECT COALESCE(MAX(scene_no), 0) AS m
                FROM script_scene_revisions WHERE script_revision_id = ?
                """,
                (script_revision_id,),
            )
            scene_no = int(row["m"]) + 1 if row else 1
        elif isinstance(scene_no, bool) or not isinstance(scene_no, int) or scene_no < 1:
            raise ValueError("scene_no must be a positive integer")

        scene_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO script_scene_revisions(
                    id, script_revision_id, scene_no, location_ref, story_time_start,
                    time_of_day, purpose, action_text, estimated_duration_ms,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scene_id,
                    script_revision_id,
                    scene_no,
                    location_ref.strip() if isinstance(location_ref, str) else None,
                    story_time_start,
                    time_of_day,
                    purpose,
                    action_text,
                    estimated_duration_ms,
                    now,
                    now,
                ),
            )
            self._mark_script_dirty(script_revision_id, now)
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_scene(scene_id)

    def update_scene(
        self,
        scene_id: str,
        *,
        purpose: str | None = None,
        action_text: str | None = None,
        time_of_day: str | None = None,
        location_ref: str | None = None,
        story_time_start: str | None = None,
        estimated_duration_ms: int | None = None,
        clear_location: bool = False,
    ) -> dict[str, Any]:
        scene = self.get_scene(scene_id)
        script = self.get_script(scene["script_revision_id"])
        self._require_editable(script)

        if purpose is not None:
            purpose = purpose.strip()
            if not purpose:
                raise ValueError("purpose must be non-empty")
        if action_text is not None:
            action_text = action_text.strip()
            if not action_text:
                raise ValueError("action_text must be non-empty")
        if time_of_day is not None and time_of_day not in TIME_OF_DAY:
            raise ValueError(f"time_of_day must be one of {sorted(TIME_OF_DAY)}")
        if estimated_duration_ms is not None:
            if (
                isinstance(estimated_duration_ms, bool)
                or not isinstance(estimated_duration_ms, int)
                or estimated_duration_ms < 0
            ):
                raise ValueError("estimated_duration_ms must be a non-negative integer")

        now = utc_now()
        next_location = None if clear_location else (
            location_ref.strip()
            if isinstance(location_ref, str)
            else scene["location_ref"]
        )
        if location_ref is None and not clear_location:
            next_location = scene["location_ref"]

        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE script_scene_revisions
                SET purpose = ?, action_text = ?, time_of_day = ?,
                    location_ref = ?, story_time_start = ?,
                    estimated_duration_ms = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    purpose if purpose is not None else scene["purpose"],
                    action_text if action_text is not None else scene["action_text"],
                    time_of_day if time_of_day is not None else scene["time_of_day"],
                    next_location,
                    story_time_start
                    if story_time_start is not None
                    else scene["story_time_start"],
                    estimated_duration_ms
                    if estimated_duration_ms is not None
                    else scene["estimated_duration_ms"],
                    now,
                    scene_id,
                ),
            )
            self._mark_script_dirty(script["id"], now)
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_scene(scene_id)

    def get_scene(self, scene_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM script_scene_revisions WHERE id = ?", (scene_id,)
        )
        if row is None:
            raise ValueError(f"scene not found: {scene_id}")
        return {
            "id": row["id"],
            "script_revision_id": row["script_revision_id"],
            "scene_no": int(row["scene_no"]),
            "location_ref": row["location_ref"],
            "story_time_start": row["story_time_start"],
            "time_of_day": row["time_of_day"],
            "purpose": row["purpose"],
            "action_text": row["action_text"],
            "estimated_duration_ms": row["estimated_duration_ms"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_scenes(self, script_revision_id: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM script_scene_revisions
            WHERE script_revision_id = ?
            ORDER BY scene_no ASC
            """,
            (script_revision_id,),
        )
        return [self.get_scene(row["id"]) for row in rows]

    # --- dialogue ---

    def add_dialogue(
        self,
        *,
        scene_revision_id: str,
        text: str,
        line_type: str = "dialogue",
        speaker_name: str | None = None,
        emotion: str | None = None,
        action_intent: str | None = None,
        pronunciation: str | None = None,
        sort_order: int | None = None,
        estimated_duration_ms: int | None = None,
    ) -> dict[str, Any]:
        scene = self.get_scene(scene_revision_id)
        script = self.get_script(scene["script_revision_id"])
        self._require_editable(script)
        text = text.strip()
        if not text:
            raise ValueError("text is required")
        if line_type not in LINE_TYPES:
            raise ValueError(f"line_type must be one of {sorted(LINE_TYPES)}")
        if line_type == "dialogue" and not (speaker_name or "").strip():
            raise ValueError("dialogue lines require speaker_name")
        if estimated_duration_ms is not None:
            if (
                isinstance(estimated_duration_ms, bool)
                or not isinstance(estimated_duration_ms, int)
                or estimated_duration_ms < 0
            ):
                raise ValueError("estimated_duration_ms must be a non-negative integer")

        if sort_order is None:
            row = self._db.fetchone(
                """
                SELECT COALESCE(MAX(sort_order), 0) AS m
                FROM dialogue_line_revisions WHERE scene_revision_id = ?
                """,
                (scene_revision_id,),
            )
            sort_order = int(row["m"]) + 1 if row else 1
        elif isinstance(sort_order, bool) or not isinstance(sort_order, int) or sort_order < 1:
            raise ValueError("sort_order must be a positive integer")

        line_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now()
        episode_id = script["episode_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO dialogue_lines(id, episode_id, created_at)
                VALUES (?, ?, ?)
                """,
                (line_id, episode_id, now),
            )
            self._db.execute(
                """
                INSERT INTO dialogue_line_revisions(
                    id, line_id, revision_no, scene_revision_id, speaker_name,
                    text, line_type, emotion, action_intent, pronunciation,
                    sort_order, estimated_duration_ms, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rev_id,
                    line_id,
                    scene_revision_id,
                    speaker_name.strip() if isinstance(speaker_name, str) else None,
                    text,
                    line_type,
                    emotion,
                    action_intent,
                    pronunciation,
                    sort_order,
                    estimated_duration_ms,
                    now,
                ),
            )
            self._mark_script_dirty(script["id"], now)
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_dialogue_revision(rev_id)

    def revise_dialogue(
        self,
        line_id: str,
        *,
        text: str | None = None,
        speaker_name: str | None = None,
        line_type: str | None = None,
        emotion: str | None = None,
        action_intent: str | None = None,
        pronunciation: str | None = None,
        sort_order: int | None = None,
        estimated_duration_ms: int | None = None,
    ) -> dict[str, Any]:
        latest = self.get_latest_dialogue(line_id)
        scene = self.get_scene(latest["scene_revision_id"])
        script = self.get_script(scene["script_revision_id"])
        self._require_editable(script)

        next_text = text.strip() if text is not None else latest["text"]
        if not next_text:
            raise ValueError("text is required")
        next_type = line_type if line_type is not None else latest["line_type"]
        if next_type not in LINE_TYPES:
            raise ValueError(f"line_type must be one of {sorted(LINE_TYPES)}")
        next_speaker = (
            speaker_name.strip()
            if isinstance(speaker_name, str)
            else latest["speaker_name"]
        )
        if speaker_name is None:
            next_speaker = latest["speaker_name"]
        if next_type == "dialogue" and not (next_speaker or "").strip():
            raise ValueError("dialogue lines require speaker_name")
        if estimated_duration_ms is not None:
            if (
                isinstance(estimated_duration_ms, bool)
                or not isinstance(estimated_duration_ms, int)
                or estimated_duration_ms < 0
            ):
                raise ValueError("estimated_duration_ms must be a non-negative integer")
        if sort_order is not None and (
            isinstance(sort_order, bool) or not isinstance(sort_order, int) or sort_order < 1
        ):
            raise ValueError("sort_order must be a positive integer")

        rev_id = str(uuid.uuid4())
        revision_no = int(latest["revision_no"]) + 1
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO dialogue_line_revisions(
                    id, line_id, revision_no, scene_revision_id, speaker_name,
                    text, line_type, emotion, action_intent, pronunciation,
                    sort_order, estimated_duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rev_id,
                    line_id,
                    revision_no,
                    latest["scene_revision_id"],
                    next_speaker,
                    next_text,
                    next_type,
                    emotion if emotion is not None else latest["emotion"],
                    action_intent
                    if action_intent is not None
                    else latest["action_intent"],
                    pronunciation
                    if pronunciation is not None
                    else latest["pronunciation"],
                    sort_order if sort_order is not None else latest["sort_order"],
                    estimated_duration_ms
                    if estimated_duration_ms is not None
                    else latest["estimated_duration_ms"],
                    now,
                ),
            )
            self._mark_script_dirty(script["id"], now)
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_dialogue_revision(rev_id)

    def get_dialogue_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM dialogue_line_revisions WHERE id = ?", (revision_id,)
        )
        if row is None:
            raise ValueError(f"dialogue revision not found: {revision_id}")
        return {
            "id": row["id"],
            "line_id": row["line_id"],
            "revision_no": int(row["revision_no"]),
            "scene_revision_id": row["scene_revision_id"],
            "speaker_name": row["speaker_name"],
            "text": row["text"],
            "line_type": row["line_type"],
            "emotion": row["emotion"],
            "action_intent": row["action_intent"],
            "pronunciation": row["pronunciation"],
            "sort_order": int(row["sort_order"]),
            "estimated_duration_ms": row["estimated_duration_ms"],
            "created_at": row["created_at"],
        }

    def get_latest_dialogue(self, line_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            """
            SELECT id FROM dialogue_line_revisions
            WHERE line_id = ?
            ORDER BY revision_no DESC
            LIMIT 1
            """,
            (line_id,),
        )
        if row is None:
            raise ValueError(f"dialogue line not found: {line_id}")
        return self.get_dialogue_revision(row["id"])

    def list_dialogue(
        self, *, scene_revision_id: str | None = None, script_revision_id: str | None = None
    ) -> list[dict[str, Any]]:
        if scene_revision_id is not None:
            rows = self._db.fetchall(
                """
                SELECT dlr.id
                FROM dialogue_line_revisions dlr
                INNER JOIN (
                    SELECT line_id, MAX(revision_no) AS max_rev
                    FROM dialogue_line_revisions
                    WHERE scene_revision_id = ?
                    GROUP BY line_id
                ) latest
                  ON dlr.line_id = latest.line_id
                 AND dlr.revision_no = latest.max_rev
                WHERE dlr.scene_revision_id = ?
                ORDER BY dlr.sort_order ASC
                """,
                (scene_revision_id, scene_revision_id),
            )
            return [self.get_dialogue_revision(row["id"]) for row in rows]

        if script_revision_id is not None:
            scenes = self.list_scenes(script_revision_id)
            result: list[dict[str, Any]] = []
            for scene in scenes:
                result.extend(self.list_dialogue(scene_revision_id=scene["id"]))
            return result

        raise ValueError("scene_revision_id or script_revision_id is required")

    # --- hooks ---

    def add_hook(
        self,
        *,
        script_revision_id: str,
        hook_type: str,
        text: str,
        position_scene_no: int | None = None,
        sort_order: int | None = None,
    ) -> dict[str, Any]:
        script = self.get_script(script_revision_id)
        self._require_editable(script)
        if hook_type not in HOOK_TYPES:
            raise ValueError(f"hook_type must be one of {sorted(HOOK_TYPES)}")
        text = text.strip()
        if not text:
            raise ValueError("text is required")
        if position_scene_no is not None and (
            isinstance(position_scene_no, bool)
            or not isinstance(position_scene_no, int)
            or position_scene_no < 1
        ):
            raise ValueError("position_scene_no must be a positive integer")

        if sort_order is None:
            row = self._db.fetchone(
                """
                SELECT COALESCE(MAX(sort_order), 0) AS m
                FROM script_hooks WHERE script_revision_id = ?
                """,
                (script_revision_id,),
            )
            sort_order = int(row["m"]) + 1 if row else 1
        elif isinstance(sort_order, bool) or not isinstance(sort_order, int) or sort_order < 1:
            raise ValueError("sort_order must be a positive integer")

        hook_id = str(uuid.uuid4())
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO script_hooks(
                    id, script_revision_id, hook_type, position_scene_no,
                    text, sort_order, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hook_id,
                    script_revision_id,
                    hook_type,
                    position_scene_no,
                    text,
                    sort_order,
                    now,
                ),
            )
            # Keep opening/ending fields in sync for the common types.
            if hook_type == "opening" and not script["opening_hook"]:
                self._db.execute(
                    """
                    UPDATE episode_script_revisions
                    SET opening_hook = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (text, now, script_revision_id),
                )
            if hook_type == "ending" and not script["ending_hook"]:
                self._db.execute(
                    """
                    UPDATE episode_script_revisions
                    SET ending_hook = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (text, now, script_revision_id),
                )
            self._mark_script_dirty(script_revision_id, now)
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_hook(hook_id)

    def get_hook(self, hook_id: str) -> dict[str, Any]:
        row = self._db.fetchone("SELECT * FROM script_hooks WHERE id = ?", (hook_id,))
        if row is None:
            raise ValueError(f"hook not found: {hook_id}")
        return {
            "id": row["id"],
            "script_revision_id": row["script_revision_id"],
            "hook_type": row["hook_type"],
            "position_scene_no": row["position_scene_no"],
            "text": row["text"],
            "sort_order": int(row["sort_order"]),
            "created_at": row["created_at"],
        }

    def list_hooks(self, script_revision_id: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM script_hooks
            WHERE script_revision_id = ?
            ORDER BY sort_order ASC
            """,
            (script_revision_id,),
        )
        return [self.get_hook(row["id"]) for row in rows]

    # --- validate / approve / tree ---

    def validate_script(self, script_id: str) -> dict[str, Any]:
        script = self.get_script(script_id)
        if script["status"] not in EDITABLE_SCRIPT_STATUSES | {"draft", "validated"}:
            if script["status"] in {"approved", "superseded"}:
                raise ValueError(f"cannot validate status: {script['status']}")
        errors = self._validation_errors(script_id)
        now = utc_now()
        status = "validated" if not errors else "draft"
        content_hash = None
        if not errors:
            tree = self.get_script_tree(script_id)
            content_hash = _hash(
                {
                    "script": {
                        k: tree["script"][k]
                        for k in (
                            "title",
                            "goal",
                            "main_conflict",
                            "twist",
                            "opening_hook",
                            "ending_hook",
                            "estimated_duration_ms",
                        )
                    },
                    "scenes": tree["scenes"],
                    "dialogue": tree["dialogue"],
                    "hooks": tree["hooks"],
                }
            )
            status = "validated"
        else:
            status = "draft"
        self._db.execute(
            """
            UPDATE episode_script_revisions
            SET status = ?, content_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, content_hash, now, script_id),
        )
        self._db.commit()
        result = self.get_script(script_id)
        result["validation_errors"] = errors
        result["valid"] = not errors
        return result

    def approve_script(self, script_id: str) -> dict[str, Any]:
        script = self.get_script(script_id)
        if script["status"] == "draft":
            validated = self.validate_script(script_id)
            if not validated["valid"]:
                raise ValueError(
                    f"script validation failed: {validated['validation_errors'][0]}"
                )
            script = self.get_script(script_id)
        if script["status"] != "validated":
            raise ValueError(f"cannot approve status: {script['status']}")

        errors = self._validation_errors(script_id)
        if errors:
            raise ValueError(f"script validation failed: {errors[0]}")

        now = utc_now()
        episode_id = script["episode_id"]
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE episode_script_revisions
                SET status = 'superseded', updated_at = ?
                WHERE episode_id = ? AND status = 'approved' AND id != ?
                """,
                (now, episode_id, script_id),
            )
            self._db.execute(
                """
                UPDATE episode_script_revisions
                SET status = 'approved', updated_at = ?
                WHERE id = ?
                """,
                (now, script_id),
            )
            title = script["title"]
            if title:
                self._db.execute(
                    """
                    UPDATE episodes
                    SET current_script_revision_id = ?, title = ?,
                        status = 'script_review', updated_at = ?
                    WHERE id = ?
                    """,
                    (script_id, title, now, episode_id),
                )
            else:
                self._db.execute(
                    """
                    UPDATE episodes
                    SET current_script_revision_id = ?,
                        status = 'script_review', updated_at = ?
                    WHERE id = ?
                    """,
                    (script_id, now, episode_id),
                )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        result = self.get_script(script_id)
        result["episode"] = self.get_episode(episode_id)
        return result

    def get_script_tree(self, script_id: str) -> dict[str, Any]:
        script = self.get_script(script_id)
        scenes = self.list_scenes(script_id)
        dialogue = self.list_dialogue(script_revision_id=script_id)
        hooks = self.list_hooks(script_id)
        return {
            "script": script,
            "scenes": scenes,
            "dialogue": dialogue,
            "hooks": hooks,
            "episode": self.get_episode(script["episode_id"]),
            "contains_media_prompts": False,
        }

    # --- internals ---

    def _require_editable(self, script: dict[str, Any]) -> None:
        if script["status"] not in EDITABLE_SCRIPT_STATUSES:
            raise ValueError(
                f"script is not editable in status: {script['status']}"
            )

    def _mark_script_dirty(self, script_id: str, now: str) -> None:
        self._db.execute(
            """
            UPDATE episode_script_revisions
            SET status = 'draft', content_hash = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, script_id),
        )

    def _validation_errors(self, script_id: str) -> list[str]:
        script = self.get_script(script_id)
        errors: list[str] = []
        if not (script["goal"] or "").strip():
            errors.append("goal is required")
        if not (script["opening_hook"] or "").strip():
            errors.append("opening_hook is required")
        if not (script["ending_hook"] or "").strip():
            errors.append("ending_hook is required")
        if not (script["main_conflict"] or "").strip():
            errors.append("main_conflict is required")
        scenes = self.list_scenes(script_id)
        if not scenes:
            errors.append("at least one scene is required")
        for scene in scenes:
            if not scene["purpose"].strip() or not scene["action_text"].strip():
                errors.append(f"scene {scene['scene_no']} needs purpose and action_text")
        dialogue = self.list_dialogue(script_revision_id=script_id)
        if not dialogue:
            errors.append("at least one dialogue or narration line is required")
        return errors
