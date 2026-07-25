from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workflow_sidecar.persistence import JobQueue, WorkspaceService


def _queue(tmp_path: Path) -> JobQueue:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    service.create_project(parent, "jobs-demo")
    queue = JobQueue(service.require_project_db())
    # keep service alive via queue attribute for cleanup in tests
    queue._service = service  # type: ignore[attr-defined]
    return queue


def test_enqueue_claim_complete_flow(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue("demo.ping", {"n": 1})
    assert job.status == "queued"

    claimed = queue.claim("worker-a", lease_seconds=30)
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.lease_owner == "worker-a"

    done = queue.complete(claimed.id, "worker-a")
    assert done.status == "succeeded"
    assert queue.claim("worker-a") is None
    queue._service.close()  # type: ignore[attr-defined]


def test_fail_retries_then_marks_failed(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue("demo.flaky", max_attempts=2)

    first = queue.claim("w1", lease_seconds=30)
    assert first is not None
    retried = queue.fail(first.id, "w1", "boom", retry=True)
    assert retried.status == "queued"
    assert retried.last_error == "boom"

    second = queue.claim("w1", lease_seconds=30)
    assert second is not None
    assert second.attempts == 2
    failed = queue.fail(second.id, "w1", "boom again", retry=True)
    assert failed.status == "failed"
    queue._service.close()  # type: ignore[attr-defined]


def test_pause_resume_and_cancel(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue("demo.pause")
    paused = queue.pause(job.id)
    assert paused.status == "paused"
    assert queue.claim("w") is None

    resumed = queue.resume(job.id)
    assert resumed.status == "queued"
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None
    cancelled = queue.cancel(claimed.id)
    assert cancelled.status == "cancelled"
    queue._service.close()  # type: ignore[attr-defined]


def test_reclaim_expired_lease(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue("demo.lease", max_attempts=3)
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None

    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    queue._db.execute(  # noqa: SLF001
        "UPDATE jobs SET lease_until = ? WHERE id = ?",
        (past, claimed.id),
    )
    queue._db.commit()  # noqa: SLF001

    count = queue.reclaim_expired()
    assert count == 1
    reclaimed = queue.get(claimed.id)
    assert reclaimed.status == "queued"
    assert reclaimed.lease_owner is None
    queue._service.close()  # type: ignore[attr-defined]


def test_wrong_worker_cannot_complete(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue("demo.own")
    claimed = queue.claim("owner", lease_seconds=30)
    assert claimed is not None
    with pytest.raises(ValueError, match="lease"):
        queue.complete(claimed.id, "intruder")
    queue._service.close()  # type: ignore[attr-defined]


def test_list_filter_and_get(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    a = queue.enqueue("a")
    b = queue.enqueue("b")
    queue.cancel(b.id)
    listed = queue.list(status="cancelled")
    assert [item.id for item in listed] == [b.id]
    assert queue.get(a.id).kind == "a"
    with pytest.raises(ValueError, match="not found"):
        queue.get("missing")
    with pytest.raises(ValueError, match="kind"):
        queue.enqueue("")
    with pytest.raises(ValueError, match="max_attempts"):
        queue.enqueue("x", max_attempts=0)
    with pytest.raises(ValueError, match="status"):
        queue.list(status="nope")
    queue._service.close()  # type: ignore[attr-defined]


def test_claim_by_kind_and_reclaim_to_failed(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    queue.enqueue("keep", max_attempts=1)
    target = queue.enqueue("take-me", max_attempts=1)
    claimed = queue.claim("w", lease_seconds=30, kinds=["take-me"])
    assert claimed is not None
    assert claimed.id == target.id

    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    queue._db.execute(  # noqa: SLF001
        "UPDATE jobs SET lease_until = ? WHERE id = ?",
        (past, claimed.id),
    )
    queue._db.commit()  # noqa: SLF001
    assert queue.reclaim_expired() == 1
    assert queue.get(claimed.id).status == "failed"
    queue._service.close()  # type: ignore[attr-defined]


def test_pause_resume_validation(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue("x")
    queue.cancel(job.id)
    with pytest.raises(ValueError, match="pause"):
        queue.pause(job.id)
    with pytest.raises(ValueError, match="resume"):
        queue.resume(job.id)
    with pytest.raises(ValueError, match="terminal"):
        queue.cancel(job.id)
    queue._service.close()  # type: ignore[attr-defined]
