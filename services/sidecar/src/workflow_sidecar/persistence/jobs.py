"""Persistent job queue with lease, retry, pause, and cancel (M1-08)."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database
from .timeutil import utc_now

VALID_STATUSES = frozenset(
    {"queued", "running", "paused", "succeeded", "failed", "cancelled"}
)
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


@dataclass
class JobRecord:
    id: str
    kind: str
    status: str
    payload: dict[str, Any]
    lease_owner: str | None
    lease_until: str | None
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _row_to_job(row: Any) -> JobRecord:
    payload_raw = row["payload_json"]
    payload = json.loads(payload_raw) if payload_raw else {}
    if not isinstance(payload, dict):
        payload = {}
    return JobRecord(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        payload=payload,
        lease_owner=row["lease_owner"],
        lease_until=row["lease_until"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class JobQueue:
    def __init__(self, db: Database) -> None:
        self._db = db

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        max_attempts: int = 3,
        job_id: str | None = None,
    ) -> JobRecord:
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("kind must be a non-empty string")
        if max_attempts < 1 or max_attempts > 20:
            raise ValueError("max_attempts must be between 1 and 20")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        now = utc_now()
        record = JobRecord(
            id=job_id or str(uuid.uuid4()),
            kind=kind.strip(),
            status="queued",
            payload=payload,
            lease_owner=None,
            lease_until=None,
            attempts=0,
            max_attempts=max_attempts,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO jobs(
                    id, kind, status, payload_json, lease_owner, lease_until,
                    attempts, max_attempts, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, NULL, 0, ?, NULL, ?, ?)
                """,
                (
                    record.id,
                    record.kind,
                    record.status,
                    json.dumps(record.payload, ensure_ascii=False),
                    record.max_attempts,
                    record.created_at,
                    record.updated_at,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return record

    def get(self, job_id: str) -> JobRecord:
        row = self._db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row is None:
            raise ValueError(f"job not found: {job_id}")
        return _row_to_job(row)

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[JobRecord]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if status is not None:
            if status not in VALID_STATUSES:
                raise ValueError(f"invalid status: {status}")
            rows = self._db.fetchall(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT * FROM jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [_row_to_job(row) for row in rows]

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        kinds: list[str] | None = None,
    ) -> JobRecord | None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string")
        if lease_seconds < 5 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 5 and 3600")

        self.reclaim_expired()
        now = utc_now()
        lease_until = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            if kinds:
                placeholders = ",".join("?" for _ in kinds)
                row = self._db.fetchone(
                    f"""
                    SELECT * FROM jobs
                    WHERE status = 'queued' AND kind IN ({placeholders})
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    tuple(kinds),
                )
            else:
                row = self._db.fetchone(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                )
            if row is None:
                self._db.connection.execute("COMMIT")
                return None

            job_id = row["id"]
            attempts = int(row["attempts"]) + 1
            self._db.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    lease_owner = ?,
                    lease_until = ?,
                    attempts = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (worker_id.strip(), lease_until, attempts, now, job_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get(job_id)

    def complete(self, job_id: str, worker_id: str) -> JobRecord:
        return self._finish(job_id, worker_id, status="succeeded", error=None)

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error: str,
        *,
        retry: bool = True,
    ) -> JobRecord:
        job = self.get(job_id)
        self._assert_owner(job, worker_id)
        if job.status != "running":
            raise ValueError(f"job is not running: {job.status}")

        now = utc_now()
        can_retry = retry and job.attempts < job.max_attempts
        next_status = "queued" if can_retry else "failed"
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE jobs
                SET status = ?,
                    lease_owner = NULL,
                    lease_until = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (next_status, error[:2000], now, job_id, worker_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get(job_id)

    def cancel(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job.status in TERMINAL:
            raise ValueError(f"job already terminal: {job.status}")
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE jobs
                SET status = 'cancelled',
                    lease_owner = NULL,
                    lease_until = NULL,
                    updated_at = ?
                WHERE id = ? AND status NOT IN ('succeeded', 'failed', 'cancelled')
                """,
                (now, job_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get(job_id)

    def pause(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job.status not in {"queued", "running"}:
            raise ValueError(f"cannot pause job in status: {job.status}")
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE jobs
                SET status = 'paused',
                    lease_owner = NULL,
                    lease_until = NULL,
                    updated_at = ?
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (now, job_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get(job_id)

    def resume(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job.status != "paused":
            raise ValueError(f"cannot resume job in status: {job.status}")
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE jobs
                SET status = 'queued',
                    updated_at = ?
                WHERE id = ? AND status = 'paused'
                """,
                (now, job_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get(job_id)

    def retry(self, job_id: str) -> JobRecord:
        """Requeue a failed (or cancelled) job for another attempt."""
        job = self.get(job_id)
        if job.status not in {"failed", "cancelled"}:
            raise ValueError(f"cannot retry job in status: {job.status}")
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE jobs
                SET status = 'queued',
                    lease_owner = NULL,
                    lease_until = NULL,
                    last_error = NULL,
                    updated_at = ?
                WHERE id = ? AND status IN ('failed', 'cancelled')
                """,
                (now, job_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get(job_id)

    def reclaim_expired(self, *, now: str | None = None) -> int:
        """Return expired running jobs to queued for retry or fail if attempts exhausted."""

        moment = now or utc_now()
        rows = self._db.fetchall(
            """
            SELECT * FROM jobs
            WHERE status = 'running'
              AND lease_until IS NOT NULL
              AND lease_until < ?
            """,
            (moment,),
        )
        reclaimed = 0
        for row in rows:
            job = _row_to_job(row)
            can_retry = job.attempts < job.max_attempts
            next_status = "queued" if can_retry else "failed"
            error = job.last_error or "lease expired"
            if not can_retry and job.last_error is None:
                error = "lease expired; max attempts reached"
            self._db.connection.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    """
                    UPDATE jobs
                    SET status = ?,
                        lease_owner = NULL,
                        lease_until = NULL,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (next_status, error[:2000], moment, job.id),
                )
                self._db.connection.execute("COMMIT")
                reclaimed += 1
            except Exception:
                self._db.connection.execute("ROLLBACK")
                raise
        return reclaimed

    def _finish(
        self,
        job_id: str,
        worker_id: str,
        *,
        status: str,
        error: str | None,
    ) -> JobRecord:
        job = self.get(job_id)
        self._assert_owner(job, worker_id)
        if job.status != "running":
            raise ValueError(f"job is not running: {job.status}")
        now = utc_now()
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                UPDATE jobs
                SET status = ?,
                    lease_owner = NULL,
                    lease_until = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (status, error, now, job_id, worker_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return self.get(job_id)

    @staticmethod
    def _assert_owner(job: JobRecord, worker_id: str) -> None:
        if job.lease_owner != worker_id:
            raise ValueError("worker does not hold the lease")
