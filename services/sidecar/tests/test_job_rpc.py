from __future__ import annotations

import asyncio
from pathlib import Path

from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_job_rpc_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> dict[str, dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(
            messages.append, global_db_path=tmp_path / "global.db"
        )
        parent = tmp_path / "projects"
        parent.mkdir()
        await runtime.handle(
            Request(
                "p1",
                "project.create",
                {"parent_dir": str(parent), "name": "Queue RPC"},
            )
        )
        await runtime.handle(
            Request(
                "e1",
                "job.enqueue",
                {"kind": "demo.work", "payload": {"x": 1}, "max_attempts": 2},
            )
        )
        await runtime.handle(
            Request("c1", "job.claim", {"worker_id": "w1", "lease_seconds": 30})
        )
        job_id = next(m for m in messages if m["id"] == "e1")["result"]["id"]
        await runtime.handle(
            Request(
                "f1",
                "job.fail",
                {"job_id": job_id, "worker_id": "w1", "error": "temp", "retry": True},
            )
        )
        await runtime.handle(
            Request("c2", "job.claim", {"worker_id": "w1", "lease_seconds": 30})
        )
        await runtime.handle(
            Request(
                "done",
                "job.complete",
                {"job_id": job_id, "worker_id": "w1"},
            )
        )
        await runtime.handle(Request("list", "job.list", {"limit": 10}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["e1"]["result"]["status"] == "queued"
    assert by_id["c1"]["result"]["job"]["status"] == "running"
    assert by_id["f1"]["result"]["status"] == "queued"
    assert by_id["done"]["result"]["status"] == "succeeded"
    assert len(by_id["list"]["result"]["jobs"]) == 1


def test_job_rpc_pause_resume_cancel_reclaim(tmp_path: Path) -> None:
    async def scenario() -> dict[str, dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(
            messages.append, global_db_path=tmp_path / "g.db"
        )
        parent = tmp_path / "p"
        parent.mkdir()
        await runtime.handle(
            Request("p", "project.create", {"parent_dir": str(parent), "name": "J"})
        )
        await runtime.handle(Request("e", "job.enqueue", {"kind": "k"}))
        job_id = next(m for m in messages if m["id"] == "e")["result"]["id"]
        await runtime.handle(Request("pause", "job.pause", {"job_id": job_id}))
        await runtime.handle(Request("resume", "job.resume", {"job_id": job_id}))
        await runtime.handle(Request("get", "job.get", {"job_id": job_id}))
        await runtime.handle(Request("cancel", "job.cancel", {"job_id": job_id}))
        await runtime.handle(Request("reclaim", "job.reclaim_expired", {}))
        await runtime.handle(Request("missing", "job.unknown", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["pause"]["result"]["status"] == "paused"
    assert by_id["resume"]["result"]["status"] == "queued"
    assert by_id["get"]["result"]["id"] == by_id["e"]["result"]["id"]
    assert by_id["cancel"]["result"]["status"] == "cancelled"
    assert by_id["reclaim"]["result"]["reclaimed"] == 0
    assert by_id["missing"]["error"]["code"] == "METHOD_NOT_FOUND"
