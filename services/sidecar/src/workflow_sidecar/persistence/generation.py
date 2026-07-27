"""Plan → execute → review generation pipeline (M2-06).

Rules:
- Execution always lands in a content draft first (M2-05).
- Review is a separate record; it never mutates execution output.
- Only review verdict pass, or explicit human accept of human_review,
  allows the draft to enter the formal revision gate (validate/promote).
- This module never creates formal_revisions directly.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .database import Database
from .drafts import DraftService, validate_against_schema, BUILTIN_SCHEMAS
from .timeutil import utc_now

RUN_STATUSES = frozenset(
    {
        "created",
        "planning",
        "planned",
        "executing",
        "reviewing",
        "approved",
        "needs_revision",
        "needs_human",
        "failed",
        "cancelled",
    }
)
REVIEW_VERDICTS = frozenset({"pass", "revise", "human_review"})


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(data: Any) -> str:
    return hashlib.sha256(_stable_json(data).encode("utf-8")).hexdigest()


class GenerationService:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._drafts = DraftService(db)

    def create_run(
        self,
        *,
        title: str,
        schema_id: str,
        intent: dict[str, Any],
        target_type: str = "episode_outline",
        target_id: str | None = None,
        branch_id: str | None = None,
        pack_lock_id: str | None = None,
        pack_lock_hash: str | None = None,
    ) -> dict[str, Any]:
        safe_title = title.strip()
        if not safe_title:
            raise ValueError("title must be a non-empty string")
        if schema_id not in BUILTIN_SCHEMAS:
            raise ValueError(f"unknown schema_id: {schema_id}")
        if not isinstance(intent, dict):
            raise ValueError("intent must be an object")
        run_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO generation_runs(
                id, target_type, target_id, branch_id, schema_id, title, intent_json,
                pack_lock_id, pack_lock_hash, status, iteration, draft_id,
                human_accept_reason, created_at, updated_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', 1, NULL, NULL, ?, ?, NULL)
            """,
            (
                run_id,
                target_type,
                target_id,
                branch_id,
                schema_id,
                safe_title,
                _stable_json(intent),
                pack_lock_id,
                pack_lock_hash,
                now,
                now,
            ),
        )
        self._db.commit()
        return self.get_run(run_id)

    def plan(
        self,
        run_id: str,
        *,
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in {"created", "needs_revision", "planned", "planning"}:
            raise ValueError(f"cannot plan from status: {run['status']}")
        now = utc_now()
        iteration = int(run["iteration"])
        if run["status"] == "needs_revision":
            iteration += 1

        intent = run["intent"]
        plan_body = plan or {
            "goal": run["title"],
            "steps": [
                {"id": "gather", "action": "collect_constraints"},
                {"id": "draft", "action": "produce_structured_output"},
                {"id": "self_check", "action": "check_required_fields"},
            ],
            "output_schema_id": run["schema_id"],
            "constraints": intent.get("constraints", []),
            "source_refs": intent.get("source_refs", []),
            "pack_lock_hash": run.get("pack_lock_hash"),
            "risks": intent.get("risks", []),
        }
        if not isinstance(plan_body, dict):
            raise ValueError("plan must be an object")
        if "output_schema_id" not in plan_body:
            raise ValueError("plan.output_schema_id is required")
        if plan_body["output_schema_id"] not in BUILTIN_SCHEMAS:
            raise ValueError("plan references unknown schema")

        fingerprint = _fingerprint(
            {
                "intent": intent,
                "plan": plan_body,
                "schema_id": run["schema_id"],
                "pack_lock_hash": run.get("pack_lock_hash"),
                "iteration": iteration,
            }
        )
        plan_id = str(uuid.uuid4())
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO generation_plans(
                    id, run_id, iteration, plan_json, input_fingerprint, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'verified', ?)
                """,
                (
                    plan_id,
                    run_id,
                    iteration,
                    _stable_json(plan_body),
                    fingerprint,
                    now,
                ),
            )
            self._db.execute(
                """
                UPDATE generation_runs
                SET status = 'planned', iteration = ?, updated_at = ?
                WHERE id = ?
                """,
                (iteration, now, run_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return {
            "plan_id": plan_id,
            "run_id": run_id,
            "iteration": iteration,
            "plan": plan_body,
            "input_fingerprint": fingerprint,
            "status": "verified",
            "created_at": now,
            "run": self.get_run(run_id),
        }

    def execute(
        self,
        run_id: str,
        *,
        output: dict[str, Any],
        executor: str = "stub.structured",
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in {"planned", "needs_revision"}:
            # Allow execute only after plan for current iteration.
            if run["status"] != "planned":
                raise ValueError(f"cannot execute from status: {run['status']}")
        if not isinstance(output, dict):
            raise ValueError("output must be an object")

        plan_row = self._db.fetchone(
            """
            SELECT * FROM generation_plans
            WHERE run_id = ? AND iteration = ?
            """,
            (run_id, run["iteration"]),
        )
        if plan_row is None:
            raise ValueError("missing verified plan for current iteration")

        now = utc_now()
        schema = BUILTIN_SCHEMAS[run["schema_id"]]
        schema_errors = validate_against_schema(output, schema)
        schema_ok = not schema_errors

        # Always create a draft; never formal revision.
        draft = self._drafts.create(
            schema_id=run["schema_id"],
            title=f"[gen] {run['title']}",
            payload=output,
            target_type=run["target_type"],
            target_id=run["target_id"] or run_id,
            branch_id=run["branch_id"],
        )
        if schema_ok:
            self._drafts.validate(draft["id"])
            draft = self._drafts.get(draft["id"])

        execution_id = str(uuid.uuid4())
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO generation_executions(
                    id, run_id, plan_id, iteration, executor, output_json,
                    draft_id, schema_ok, status, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    run_id,
                    plan_row["id"],
                    run["iteration"],
                    executor,
                    _stable_json(output),
                    draft["id"],
                    1 if schema_ok else 0,
                    "completed" if schema_ok else "schema_failed",
                    None if schema_ok else _stable_json(schema_errors),
                    now,
                ),
            )
            self._db.execute(
                """
                UPDATE generation_runs
                SET status = 'reviewing', draft_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (draft["id"], now, run_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise

        return {
            "execution_id": execution_id,
            "run_id": run_id,
            "iteration": run["iteration"],
            "draft_id": draft["id"],
            "draft_status": draft["status"],
            "schema_ok": schema_ok,
            "schema_errors": schema_errors,
            "status": "completed" if schema_ok else "schema_failed",
            "run": self.get_run(run_id),
        }

    def review(
        self,
        run_id: str,
        *,
        verdict: str,
        findings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] != "reviewing":
            raise ValueError(f"cannot review from status: {run['status']}")
        if verdict not in REVIEW_VERDICTS:
            raise ValueError(f"invalid verdict: {verdict}")
        findings = findings or []
        if not isinstance(findings, list):
            raise ValueError("findings must be an array")

        exec_row = self._db.fetchone(
            """
            SELECT * FROM generation_executions
            WHERE run_id = ? AND iteration = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id, run["iteration"]),
        )
        if exec_row is None:
            raise ValueError("missing execution for current iteration")

        now = utc_now()
        review_id = str(uuid.uuid4())
        if verdict == "pass":
            next_status = "approved"
        elif verdict == "revise":
            next_status = "needs_revision"
        else:
            next_status = "needs_human"

        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO generation_reviews(
                    id, run_id, execution_id, iteration, verdict,
                    findings_json, isolated, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    review_id,
                    run_id,
                    exec_row["id"],
                    run["iteration"],
                    verdict,
                    _stable_json(findings),
                    now,
                ),
            )
            finished = now if next_status == "approved" else None
            self._db.execute(
                """
                UPDATE generation_runs
                SET status = ?, updated_at = ?, finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (next_status, now, finished, run_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise

        return {
            "review_id": review_id,
            "run_id": run_id,
            "iteration": run["iteration"],
            "verdict": verdict,
            "findings": findings,
            "isolated": True,
            "run": self.get_run(run_id),
            # Explicit: review never promotes formal revisions.
            "formal_revision_created": False,
        }

    def accept_human_review(self, run_id: str, *, reason: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] != "needs_human":
            raise ValueError("human accept only allowed when status is needs_human")
        safe_reason = reason.strip()
        if not safe_reason:
            raise ValueError("reason is required for human accept")
        now = utc_now()
        self._db.execute(
            """
            UPDATE generation_runs
            SET status = 'approved', human_accept_reason = ?, updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (safe_reason, now, now, run_id),
        )
        self._db.commit()
        return self.get_run(run_id)

    def open_draft_gate(self, run_id: str) -> dict[str, Any]:
        """Allow draft.validate/promote only after approved generation.

        Still does not create formal revisions; caller must use draft.promote.
        """
        run = self.get_run(run_id)
        if run["status"] != "approved":
            raise ValueError(
                "draft gate opens only after generation review pass or human accept"
            )
        if not run["draft_id"]:
            raise ValueError("run has no draft")
        draft = self._drafts.get(run["draft_id"])
        if draft["status"] == "draft":
            draft = self._drafts.validate(run["draft_id"])
        return {
            "run_id": run_id,
            "draft": draft,
            "can_promote": draft["status"] == "validated",
            "formal_revision_created": False,
            "note": "Call draft.promote separately; generation never writes formal revisions.",
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self._db.fetchone(
            "SELECT * FROM generation_runs WHERE id = ?", (run_id,)
        )
        if row is None:
            raise ValueError(f"generation run not found: {run_id}")
        return {
            "id": row["id"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "branch_id": row["branch_id"],
            "schema_id": row["schema_id"],
            "title": row["title"],
            "intent": json.loads(row["intent_json"]),
            "pack_lock_id": row["pack_lock_id"],
            "pack_lock_hash": row["pack_lock_hash"],
            "status": row["status"],
            "iteration": int(row["iteration"]),
            "draft_id": row["draft_id"],
            "human_accept_reason": row["human_accept_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "finished_at": row["finished_at"],
        }

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self._db.fetchall(
            """
            SELECT id FROM generation_runs
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [self.get_run(row["id"]) for row in rows]

    def get_history(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        plans = self._db.fetchall(
            "SELECT * FROM generation_plans WHERE run_id = ? ORDER BY iteration",
            (run_id,),
        )
        executions = self._db.fetchall(
            "SELECT * FROM generation_executions WHERE run_id = ? ORDER BY iteration",
            (run_id,),
        )
        reviews = self._db.fetchall(
            "SELECT * FROM generation_reviews WHERE run_id = ? ORDER BY iteration",
            (run_id,),
        )
        return {
            "run": run,
            "plans": [
                {
                    "id": row["id"],
                    "iteration": int(row["iteration"]),
                    "plan": json.loads(row["plan_json"]),
                    "input_fingerprint": row["input_fingerprint"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
                for row in plans
            ],
            "executions": [
                {
                    "id": row["id"],
                    "iteration": int(row["iteration"]),
                    "plan_id": row["plan_id"],
                    "executor": row["executor"],
                    "output": json.loads(row["output_json"]),
                    "draft_id": row["draft_id"],
                    "schema_ok": bool(row["schema_ok"]),
                    "status": row["status"],
                    "error": row["error"],
                    "created_at": row["created_at"],
                }
                for row in executions
            ],
            "reviews": [
                {
                    "id": row["id"],
                    "iteration": int(row["iteration"]),
                    "execution_id": row["execution_id"],
                    "verdict": row["verdict"],
                    "findings": json.loads(row["findings_json"]),
                    "isolated": bool(row["isolated"]),
                    "created_at": row["created_at"],
                }
                for row in reviews
            ],
        }
