"""Temporal continuity state ledger and conflict checks (M2-12).

States are interval-valued (story time ordinals), never overwrite in place.
Equal-priority overlapping intervals on the same subject+key are blockers.
Effective state at a time is the highest-priority active covering record.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import Database
from .timeutil import utc_now

SUBJECT_TYPES = frozenset({"character", "location", "prop", "relationship"})
STATE_STATUSES = frozenset({"active", "superseded"})
# Common keys for validation guidance; open set allowed.
KNOWN_KEYS = frozenset(
    {
        "outfit",
        "injury",
        "owner",
        "location",
        "hair",
        "age",
        "emotion",
        "presence",
        "held_prop",
        "damage",
        "open_closed",
        "other",
    }
)


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


def _intervals_overlap(
    a_from: int, a_to: int | None, b_from: int, b_to: int | None
) -> bool:
    """Half-open intervals [from, to); None to means +inf."""

    a_end = a_to if a_to is not None else 2**62
    b_end = b_to if b_to is not None else 2**62
    return a_from < b_end and b_from < a_end


def _covers(time_ord: int, from_ord: int, to_ord: int | None) -> bool:
    if time_ord < from_ord:
        return False
    if to_ord is None:
        return True
    return time_ord < to_ord


class ContinuityService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add_state(
        self,
        *,
        branch_id: str,
        subject_type: str,
        subject_id: str,
        state_key: str,
        value: Any,
        story_time_from: str,
        time_from_ord: int,
        story_time_to: str | None = None,
        time_to_ord: int | None = None,
        source_revision_id: str | None = None,
        source_type: str = "user",
        priority: int = 0,
        allow_equal_priority_overlap: bool = False,
    ) -> dict[str, Any]:
        if subject_type not in SUBJECT_TYPES:
            raise ValueError(f"subject_type must be one of {sorted(SUBJECT_TYPES)}")
        state_key = state_key.strip()
        if not state_key:
            raise ValueError("state_key is required")
        story_time_from = story_time_from.strip()
        if not story_time_from:
            raise ValueError("story_time_from is required")
        if (
            isinstance(time_from_ord, bool)
            or not isinstance(time_from_ord, int)
        ):
            raise ValueError("time_from_ord must be an integer")
        if time_to_ord is not None:
            if isinstance(time_to_ord, bool) or not isinstance(time_to_ord, int):
                raise ValueError("time_to_ord must be an integer or null")
            if time_to_ord <= time_from_ord:
                raise ValueError("time_to_ord must be greater than time_from_ord")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("priority must be an integer")
        if not isinstance(subject_id, str) or not subject_id:
            raise ValueError("subject_id must be a non-empty string")

        # Pre-check equal-priority overlaps.
        blockers = self._find_overlaps(
            branch_id=branch_id,
            subject_type=subject_type,
            subject_id=subject_id,
            state_key=state_key,
            time_from_ord=time_from_ord,
            time_to_ord=time_to_ord,
            priority=priority,
            exclude_id=None,
            equal_priority_only=True,
        )
        if blockers and not allow_equal_priority_overlap:
            raise ValueError(
                f"equal-priority continuity conflict with state {blockers[0]['id']}: "
                f"{blockers[0]['message']}"
            )

        state_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO continuity_states(
                id, branch_id, subject_type, subject_id, state_key, value_json,
                story_time_from, story_time_to, time_from_ord, time_to_ord,
                source_revision_id, source_type, priority, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                state_id,
                branch_id,
                subject_type,
                subject_id,
                state_key,
                _stable_json(value),
                story_time_from,
                story_time_to,
                time_from_ord,
                time_to_ord,
                source_revision_id,
                source_type,
                priority,
                now,
                now,
            ),
        )
        self._db.commit()

        # Persist open conflict reports for audit when forced.
        if blockers and allow_equal_priority_overlap:
            for item in blockers:
                self._record_conflict(
                    branch_id=branch_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    state_key=state_key,
                    state_a_id=state_id,
                    state_b_id=item["id"],
                    severity="blocker",
                    message=item["message"],
                )

        return self.get_state(state_id)

    def end_state(
        self,
        state_id: str,
        *,
        story_time_to: str,
        time_to_ord: int,
    ) -> dict[str, Any]:
        state = self.get_state(state_id)
        if state["status"] != "active":
            raise ValueError(f"cannot end status: {state['status']}")
        if state["time_to_ord"] is not None:
            raise ValueError("state already has an end time")
        story_time_to = story_time_to.strip()
        if not story_time_to:
            raise ValueError("story_time_to is required")
        if isinstance(time_to_ord, bool) or not isinstance(time_to_ord, int):
            raise ValueError("time_to_ord must be an integer")
        if time_to_ord <= state["time_from_ord"]:
            raise ValueError("time_to_ord must be greater than time_from_ord")

        # Ending may create or remove overlaps with other open ranges — recheck.
        blockers = self._find_overlaps(
            branch_id=state["branch_id"],
            subject_type=state["subject_type"],
            subject_id=state["subject_id"],
            state_key=state["state_key"],
            time_from_ord=state["time_from_ord"],
            time_to_ord=time_to_ord,
            priority=state["priority"],
            exclude_id=state_id,
            equal_priority_only=True,
        )
        if blockers:
            raise ValueError(
                f"ending would leave equal-priority conflict with {blockers[0]['id']}"
            )

        now = utc_now()
        self._db.execute(
            """
            UPDATE continuity_states
            SET story_time_to = ?, time_to_ord = ?, updated_at = ?
            WHERE id = ?
            """,
            (story_time_to, time_to_ord, now, state_id),
        )
        self._db.commit()
        return self.get_state(state_id)

    def supersede_state(self, state_id: str, *, reason: str | None = None) -> dict[str, Any]:
        state = self.get_state(state_id)
        if state["status"] != "active":
            raise ValueError(f"cannot supersede status: {state['status']}")
        now = utc_now()
        self._db.execute(
            """
            UPDATE continuity_states
            SET status = 'superseded', updated_at = ?
            WHERE id = ?
            """,
            (now, state_id),
        )
        # Resolve related open conflicts.
        self._db.execute(
            """
            UPDATE continuity_conflict_reports
            SET status = 'resolved', resolved_at = ?
            WHERE status = 'open'
              AND (state_a_id = ? OR state_b_id = ?)
            """,
            (now, state_id, state_id),
        )
        self._db.commit()
        result = self.get_state(state_id)
        result["supersede_reason"] = reason
        return result

    def get_state(self, state_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM continuity_states WHERE id = ?", (state_id,)
        )
        if row is None:
            raise ValueError(f"continuity state not found: {state_id}")
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "subject_type": row["subject_type"],
            "subject_id": row["subject_id"],
            "state_key": row["state_key"],
            "value": json.loads(row["value_json"]),
            "story_time_from": row["story_time_from"],
            "story_time_to": row["story_time_to"],
            "time_from_ord": int(row["time_from_ord"]),
            "time_to_ord": (
                int(row["time_to_ord"]) if row["time_to_ord"] is not None else None
            ),
            "source_revision_id": row["source_revision_id"],
            "source_type": row["source_type"],
            "priority": int(row["priority"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_states(
        self,
        *,
        branch_id: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        state_key: str | None = None,
        active_only: bool = True,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        clauses = ["branch_id = ?"]
        args: list[Any] = [branch_id]
        if active_only:
            clauses.append("status = 'active'")
        if subject_type is not None:
            clauses.append("subject_type = ?")
            args.append(subject_type)
        if subject_id is not None:
            clauses.append("subject_id = ?")
            args.append(subject_id)
        if state_key is not None:
            clauses.append("state_key = ?")
            args.append(state_key)
        args.append(limit)
        rows = self._db.fetchall(
            f"""
            SELECT id FROM continuity_states
            WHERE {' AND '.join(clauses)}
            ORDER BY time_from_ord ASC, priority DESC, created_at ASC
            LIMIT ?
            """,
            tuple(args),
        )
        return [self.get_state(row["id"]) for row in rows]

    def effective_at(
        self,
        *,
        branch_id: str,
        subject_type: str,
        subject_id: str,
        state_key: str,
        at_time_ord: int,
    ) -> dict[str, Any] | None:
        if isinstance(at_time_ord, bool) or not isinstance(at_time_ord, int):
            raise ValueError("at_time_ord must be an integer")
        states = self.list_states(
            branch_id=branch_id,
            subject_type=subject_type,
            subject_id=subject_id,
            state_key=state_key,
            active_only=True,
            limit=500,
        )
        covering = [
            s
            for s in states
            if _covers(at_time_ord, s["time_from_ord"], s["time_to_ord"])
        ]
        if not covering:
            return None
        covering.sort(key=lambda s: (s["priority"], s["created_at"]), reverse=True)
        return covering[0]

    def resolve_all_effective(
        self,
        *,
        branch_id: str,
        at_time_ord: int,
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve effective state for each subject+key at a story time."""

        states = self.list_states(
            branch_id=branch_id,
            subject_type=subject_type,
            subject_id=subject_id,
            active_only=True,
            limit=500,
        )
        covering = [
            s
            for s in states
            if _covers(at_time_ord, s["time_from_ord"], s["time_to_ord"])
        ]
        best: dict[tuple[str, str, str], dict[str, Any]] = {}
        for state in covering:
            key = (state["subject_type"], state["subject_id"], state["state_key"])
            prev = best.get(key)
            if prev is None or (state["priority"], state["created_at"]) > (
                prev["priority"],
                prev["created_at"],
            ):
                best[key] = state
        return list(best.values())

    def check_conflicts(
        self, *, branch_id: str, persist: bool = False
    ) -> dict[str, Any]:
        states = self.list_states(branch_id=branch_id, active_only=True, limit=500)
        conflicts: list[dict[str, Any]] = []
        for i, a in enumerate(states):
            for b in states[i + 1 :]:
                if (
                    a["subject_type"] != b["subject_type"]
                    or a["subject_id"] != b["subject_id"]
                    or a["state_key"] != b["state_key"]
                ):
                    continue
                if not _intervals_overlap(
                    a["time_from_ord"],
                    a["time_to_ord"],
                    b["time_from_ord"],
                    b["time_to_ord"],
                ):
                    continue
                if a["priority"] == b["priority"]:
                    severity = "blocker"
                    message = (
                        f"equal priority {a['priority']} overlap on "
                        f"{a['subject_type']}/{a['subject_id']}/{a['state_key']} "
                        f"between {a['story_time_from']} and {b['story_time_from']}"
                    )
                else:
                    severity = "warning"
                    message = (
                        f"priority overlap ({a['priority']} vs {b['priority']}) on "
                        f"{a['subject_type']}/{a['subject_id']}/{a['state_key']}; "
                        f"higher priority wins at query time"
                    )
                item = {
                    "state_a_id": a["id"],
                    "state_b_id": b["id"],
                    "subject_type": a["subject_type"],
                    "subject_id": a["subject_id"],
                    "state_key": a["state_key"],
                    "severity": severity,
                    "message": message,
                }
                conflicts.append(item)
                if persist:
                    self._record_conflict(
                        branch_id=branch_id,
                        subject_type=a["subject_type"],
                        subject_id=a["subject_id"],
                        state_key=a["state_key"],
                        state_a_id=a["id"],
                        state_b_id=b["id"],
                        severity=severity,
                        message=message,
                    )
        blockers = [c for c in conflicts if c["severity"] == "blocker"]
        return {
            "branch_id": branch_id,
            "conflicts": conflicts,
            "blocker_count": len(blockers),
            "warning_count": len(conflicts) - len(blockers),
            "blocked": len(blockers) > 0,
        }

    def list_conflict_reports(
        self, *, branch_id: str, open_only: bool = True, limit: int = 100
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if open_only:
            rows = self._db.fetchall(
                """
                SELECT * FROM continuity_conflict_reports
                WHERE branch_id = ? AND status = 'open'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (branch_id, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT * FROM continuity_conflict_reports
                WHERE branch_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (branch_id, limit),
            )
        return [
            {
                "id": row["id"],
                "branch_id": row["branch_id"],
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "state_key": row["state_key"],
                "state_a_id": row["state_a_id"],
                "state_b_id": row["state_b_id"],
                "severity": row["severity"],
                "message": row["message"],
                "status": row["status"],
                "created_at": row["created_at"],
                "resolved_at": row["resolved_at"],
            }
            for row in rows
        ]

    def create_snapshot(
        self,
        *,
        branch_id: str,
        at_story_time: str,
        at_time_ord: int,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        at_story_time = at_story_time.strip()
        if not at_story_time:
            raise ValueError("at_story_time is required")
        if isinstance(at_time_ord, bool) or not isinstance(at_time_ord, int):
            raise ValueError("at_time_ord must be an integer")
        report = self.check_conflicts(branch_id=branch_id, persist=False)
        if report["blocked"]:
            raise ValueError(
                f"cannot snapshot: {report['blocker_count']} equal-priority conflicts"
            )
        effective = self.resolve_all_effective(
            branch_id=branch_id, at_time_ord=at_time_ord
        )
        payload = [
            {
                "subject_type": s["subject_type"],
                "subject_id": s["subject_id"],
                "state_key": s["state_key"],
                "value": s["value"],
                "priority": s["priority"],
                "source_state_id": s["id"],
                "story_time_from": s["story_time_from"],
                "story_time_to": s["story_time_to"],
            }
            for s in effective
        ]
        snap_id = str(uuid.uuid4())
        now = utc_now()
        content_hash = _hash(payload)
        self._db.execute(
            """
            INSERT INTO continuity_snapshots(
                id, branch_id, at_story_time, at_time_ord, purpose,
                states_json, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap_id,
                branch_id,
                at_story_time,
                at_time_ord,
                purpose,
                _stable_json(payload),
                content_hash,
                now,
            ),
        )
        self._db.commit()
        return self.get_snapshot(snap_id)

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM continuity_snapshots WHERE id = ?", (snapshot_id,)
        )
        if row is None:
            raise ValueError(f"continuity snapshot not found: {snapshot_id}")
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "at_story_time": row["at_story_time"],
            "at_time_ord": int(row["at_time_ord"]),
            "purpose": row["purpose"],
            "states": json.loads(row["states_json"]),
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
            "immutable": True,
        }

    def list_snapshots(
        self, *, branch_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self._db.fetchall(
            """
            SELECT id FROM continuity_snapshots
            WHERE branch_id = ?
            ORDER BY at_time_ord DESC, created_at DESC
            LIMIT ?
            """,
            (branch_id, limit),
        )
        return [self.get_snapshot(row["id"]) for row in rows]

    def ledger_overview(self, branch_id: str) -> dict[str, Any]:
        states = self.list_states(branch_id=branch_id, active_only=True, limit=500)
        report = self.check_conflicts(branch_id=branch_id, persist=False)
        snaps = self.list_snapshots(branch_id=branch_id, limit=20)
        return {
            "branch_id": branch_id,
            "active_state_count": len(states),
            "states": states[:50],
            "conflicts": report,
            "snapshots": snaps,
            "known_state_keys": sorted(KNOWN_KEYS),
        }

    # --- internals ---

    def _find_overlaps(
        self,
        *,
        branch_id: str,
        subject_type: str,
        subject_id: str,
        state_key: str,
        time_from_ord: int,
        time_to_ord: int | None,
        priority: int,
        exclude_id: str | None,
        equal_priority_only: bool,
    ) -> list[dict[str, str]]:
        states = self.list_states(
            branch_id=branch_id,
            subject_type=subject_type,
            subject_id=subject_id,
            state_key=state_key,
            active_only=True,
            limit=500,
        )
        hits: list[dict[str, str]] = []
        for other in states:
            if exclude_id and other["id"] == exclude_id:
                continue
            if equal_priority_only and other["priority"] != priority:
                continue
            if _intervals_overlap(
                time_from_ord,
                time_to_ord,
                other["time_from_ord"],
                other["time_to_ord"],
            ):
                hits.append(
                    {
                        "id": other["id"],
                        "message": (
                            f"overlaps {other['story_time_from']}"
                            f"→{other['story_time_to'] or '∞'} "
                            f"(priority {other['priority']})"
                        ),
                    }
                )
        return hits

    def _record_conflict(
        self,
        *,
        branch_id: str,
        subject_type: str,
        subject_id: str,
        state_key: str,
        state_a_id: str,
        state_b_id: str,
        severity: str,
        message: str,
    ) -> None:
        # Deduplicate open pair.
        existing = self._db.fetchone(
            """
            SELECT id FROM continuity_conflict_reports
            WHERE status = 'open'
              AND (
                (state_a_id = ? AND state_b_id = ?)
                OR (state_a_id = ? AND state_b_id = ?)
              )
            LIMIT 1
            """,
            (state_a_id, state_b_id, state_b_id, state_a_id),
        )
        if existing is not None:
            return
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO continuity_conflict_reports(
                id, branch_id, subject_type, subject_id, state_key,
                state_a_id, state_b_id, severity, message, status,
                created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL)
            """,
            (
                str(uuid.uuid4()),
                branch_id,
                subject_type,
                subject_id,
                state_key,
                state_a_id,
                state_b_id,
                severity,
                message,
                now,
            ),
        )
        self._db.commit()
