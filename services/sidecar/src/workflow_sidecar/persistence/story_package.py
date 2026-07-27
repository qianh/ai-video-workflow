"""Story package, world rules, and season timeline (M2-07).

Story packages are production exports of narrative structure only.
They never embed media prompts or shot parameters.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import Database
from .timeutil import utc_now

FORCE_LEVELS = frozenset({"hard", "soft"})
EPISODE_STATUSES = frozenset(
    {
        "planned",
        "scripting",
        "script_review",
        "preproduction",
        "generating",
        "qc_review",
        "rough_cut_review",
        "approved",
        "exported",
        "published",
        "paused",
        "blocked",
        "archived",
    }
)
PACKAGE_STATUSES = frozenset({"draft", "validated", "approved", "superseded"})


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


class StoryPackageService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- world rules ---

    def add_world_rule(
        self,
        *,
        branch_id: str,
        category: str,
        rule_text: str,
        force_level: str = "soft",
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if force_level not in FORCE_LEVELS:
            raise ValueError("force_level must be hard or soft")
        category = category.strip()
        rule_text = rule_text.strip()
        if not category or not rule_text:
            raise ValueError("category and rule_text are required")
        rule_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO world_rules(
                id, branch_id, category, rule_text, force_level, scope_json,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                rule_id,
                branch_id,
                category,
                rule_text,
                force_level,
                _stable_json(scope or {}),
                now,
                now,
            ),
        )
        self._db.commit()
        return self.get_world_rule(rule_id)

    def get_world_rule(self, rule_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM world_rules WHERE id = ?", (rule_id,)
        )
        if row is None:
            raise ValueError(f"world rule not found: {rule_id}")
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "category": row["category"],
            "rule_text": row["rule_text"],
            "force_level": row["force_level"],
            "scope": json.loads(row["scope_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_world_rules(self, branch_id: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM world_rules
            WHERE branch_id = ? AND status = 'active'
            ORDER BY category, created_at
            """,
            (branch_id,),
        )
        return [self.get_world_rule(row["id"]) for row in rows]

    def check_hard_rule_conflicts(
        self, branch_id: str, claims: list[str]
    ) -> list[dict[str, str]]:
        """Naive hard-rule blocker: claim text contains forbidden phrase from hard rules."""

        if not isinstance(claims, list):
            raise ValueError("claims must be an array")
        rules = self.list_world_rules(branch_id)
        conflicts: list[dict[str, str]] = []
        for rule in rules:
            if rule["force_level"] != "hard":
                continue
            needle = rule["rule_text"]
            for claim in claims:
                if not isinstance(claim, str):
                    continue
                # Convention: hard rules may encode "forbid:xxx"
                if needle.startswith("forbid:") and needle[7:].strip() in claim:
                    conflicts.append(
                        {
                            "rule_id": rule["id"],
                            "force_level": "hard",
                            "message": f"hard rule forbids '{needle[7:].strip()}'",
                        }
                    )
        return conflicts

    # --- season timeline ---

    def add_timeline_beat(
        self,
        *,
        branch_id: str,
        beat_no: int,
        title: str,
        summary: str,
        story_time: str | None = None,
        arc_tag: str | None = None,
        episode_nos: list[int] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(beat_no, int) or isinstance(beat_no, bool) or beat_no < 1:
            raise ValueError("beat_no must be a positive integer")
        title = title.strip()
        summary = summary.strip()
        if not title or not summary:
            raise ValueError("title and summary are required")
        beat_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO season_timeline_beats(
                id, branch_id, beat_no, title, summary, story_time, arc_tag,
                episode_nos_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                beat_id,
                branch_id,
                beat_no,
                title,
                summary,
                story_time,
                arc_tag,
                _stable_json(episode_nos or []),
                now,
                now,
            ),
        )
        self._db.commit()
        return self.get_timeline_beat(beat_id)

    def get_timeline_beat(self, beat_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM season_timeline_beats WHERE id = ?", (beat_id,)
        )
        if row is None:
            raise ValueError(f"timeline beat not found: {beat_id}")
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "beat_no": int(row["beat_no"]),
            "title": row["title"],
            "summary": row["summary"],
            "story_time": row["story_time"],
            "arc_tag": row["arc_tag"],
            "episode_nos": json.loads(row["episode_nos_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_timeline(self, branch_id: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM season_timeline_beats
            WHERE branch_id = ?
            ORDER BY beat_no ASC
            """,
            (branch_id,),
        )
        return [self.get_timeline_beat(row["id"]) for row in rows]

    # --- episodes ---

    def ensure_episodes(
        self,
        *,
        branch_id: str,
        count: int,
        title_prefix: str = "第",
    ) -> list[dict[str, Any]]:
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("count must be a positive integer")
        if count > 100:
            raise ValueError("count must be <= 100")
        now = utc_now()
        created: list[dict[str, Any]] = []
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            for episode_no in range(1, count + 1):
                existing = self._db.fetchone(
                    """
                    SELECT id FROM episodes
                    WHERE branch_id = ? AND episode_no = ?
                    """,
                    (branch_id, episode_no),
                )
                if existing is not None:
                    created.append(self.get_episode(str(existing["id"])))
                    continue
                episode_id = str(uuid.uuid4())
                title = f"{title_prefix}{episode_no}集"
                self._db.execute(
                    """
                    INSERT INTO episodes(
                        id, branch_id, episode_no, title, status,
                        current_script_revision_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'planned', NULL, ?, ?)
                    """,
                    (episode_id, branch_id, episode_no, title, now, now),
                )
                created.append(self.get_episode(episode_id))
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return created

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

    def list_episodes(self, branch_id: str) -> list[dict[str, Any]]:
        rows = self._db.fetchall(
            """
            SELECT id FROM episodes
            WHERE branch_id = ?
            ORDER BY episode_no ASC
            """,
            (branch_id,),
        )
        return [self.get_episode(row["id"]) for row in rows]

    # --- story package ---

    def create_package_revision(
        self,
        *,
        branch_id: str,
        name: str,
        positioning: dict[str, Any],
        world_rule_ids: list[str] | None = None,
        timeline_beat_ids: list[str] | None = None,
        episode_ids: list[str] | None = None,
        pack_lock_id: str | None = None,
        notes: str | None = None,
        claims_for_rules: list[str] | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(positioning, dict) or not positioning.get("theme"):
            raise ValueError("positioning.theme is required")

        world_rule_ids = world_rule_ids or []
        timeline_beat_ids = timeline_beat_ids or []
        episode_ids = episode_ids or []
        for rule_id in world_rule_ids:
            self.get_world_rule(rule_id)
        for beat_id in timeline_beat_ids:
            self.get_timeline_beat(beat_id)
        for episode_id in episode_ids:
            ep = self.get_episode(episode_id)
            if ep["branch_id"] != branch_id:
                raise ValueError("episode belongs to another branch")

        conflicts = self.check_hard_rule_conflicts(
            branch_id, claims_for_rules or []
        )
        if conflicts:
            raise ValueError(f"hard world rule conflict: {conflicts[0]['message']}")

        payload = {
            "positioning": positioning,
            "world_rule_ids": world_rule_ids,
            "timeline_beat_ids": timeline_beat_ids,
            "episode_ids": episode_ids,
            "pack_lock_id": pack_lock_id,
        }
        content_hash = _hash(payload)
        package_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        now = utc_now()

        # Validate minimum structure for a usable package.
        status = "validated"
        if not timeline_beat_ids or not episode_ids:
            status = "draft"

        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO story_packages(id, branch_id, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (package_id, branch_id, name, now),
            )
            self._db.execute(
                """
                INSERT INTO story_package_revisions(
                    id, package_id, revision_no, branch_id, status,
                    positioning_json, world_rule_ids_json, timeline_beat_ids_json,
                    episode_ids_json, pack_lock_id, content_hash, notes, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    package_id,
                    branch_id,
                    status,
                    _stable_json(positioning),
                    _stable_json(world_rule_ids),
                    _stable_json(timeline_beat_ids),
                    _stable_json(episode_ids),
                    pack_lock_id,
                    content_hash,
                    notes,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get_package_revision(revision_id)

    def approve_package_revision(self, revision_id: str) -> dict[str, Any]:
        rev = self.get_package_revision(revision_id)
        if rev["status"] not in {"validated", "draft"}:
            raise ValueError(f"cannot approve status: {rev['status']}")
        if rev["status"] == "draft":
            raise ValueError("draft package needs timeline and episodes before approve")
        if not rev["timeline_beat_ids"] or not rev["episode_ids"]:
            raise ValueError("package requires timeline beats and episodes")
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE story_package_revisions
                SET status = 'superseded'
                WHERE package_id = ? AND status = 'approved' AND id != ?
                """,
                (rev["package_id"], revision_id),
            )
            self._db.execute(
                """
                UPDATE story_package_revisions
                SET status = 'approved'
                WHERE id = ?
                """,
                (revision_id,),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        result = self.get_package_revision(revision_id)
        result["updated_at"] = now
        return result

    def get_package_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM story_package_revisions WHERE id = ?",
            (revision_id,),
        )
        if row is None:
            raise ValueError(f"package revision not found: {revision_id}")
        return {
            "id": row["id"],
            "package_id": row["package_id"],
            "revision_no": int(row["revision_no"]),
            "branch_id": row["branch_id"],
            "status": row["status"],
            "positioning": json.loads(row["positioning_json"]),
            "world_rule_ids": json.loads(row["world_rule_ids_json"]),
            "timeline_beat_ids": json.loads(row["timeline_beat_ids_json"]),
            "episode_ids": json.loads(row["episode_ids_json"]),
            "pack_lock_id": row["pack_lock_id"],
            "content_hash": row["content_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            # Explicit: packages never embed media prompts.
            "contains_media_prompts": False,
        }

    def list_package_revisions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self._db.fetchall(
            """
            SELECT id FROM story_package_revisions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [self.get_package_revision(row["id"]) for row in rows]

    def season_overview(self, branch_id: str) -> dict[str, Any]:
        return {
            "branch_id": branch_id,
            "world_rules": self.list_world_rules(branch_id),
            "timeline": self.list_timeline(branch_id),
            "episodes": self.list_episodes(branch_id),
            "packages": [
                item
                for item in self.list_package_revisions(limit=20)
                if item["branch_id"] == branch_id
            ],
        }
