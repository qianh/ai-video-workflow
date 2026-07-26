from __future__ import annotations

import asyncio
from pathlib import Path

from workflow_sidecar.persistence import JobQueue, WorkspaceService
from workflow_sidecar.persistence.overview import build_project_overview
from workflow_sidecar.persistence.snapshots import create_db_snapshot
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_build_project_overview_counts_disk_and_failures(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Overview")
    root = Path(project.root_path)
    (root / "assets" / "documents" / "a.txt").write_text("hello", encoding="utf-8")
    create_db_snapshot(root, reason="overview-test")

    queue = JobQueue(service.require_project_db())
    ok = queue.enqueue("demo.ok")
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None
    queue.complete(ok.id, "w")
    bad = queue.enqueue("demo.bad", max_attempts=1)
    claimed_bad = queue.claim("w", lease_seconds=30)
    assert claimed_bad is not None
    queue.fail(bad.id, "w", "nope", retry=False)

    overview = build_project_overview(project=project, jobs=queue)
    assert overview["job_counts"]["succeeded"] == 1
    assert overview["job_counts"]["failed"] == 1
    assert overview["queue_depth"] == 0
    assert overview["disk"]["assets_bytes"] > 0
    assert overview["disk"]["total_bytes"] > 0
    assert len(overview["snapshots"]) >= 1
    assert overview["failed_jobs"][0]["kind"] == "demo.bad"
    service.close()


def test_project_overview_rpc(tmp_path: Path) -> None:
    async def scenario() -> dict[str, object]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(
            messages.append, global_db_path=tmp_path / "g.db"
        )
        parent = tmp_path / "p"
        parent.mkdir()
        await runtime.handle(
            Request(
                "c",
                "project.create",
                {"parent_dir": str(parent), "name": "OV"},
            )
        )
        await runtime.handle(
            Request("e", "job.enqueue", {"kind": "x", "max_attempts": 1})
        )
        await runtime.handle(Request("o", "project.overview", {}))
        await runtime.shutdown()
        return next(m for m in messages if m["id"] == "o")

    message = run(scenario())
    assert message["type"] == "response"
    assert message["result"]["job_counts"]["queued"] == 1
    assert "disk" in message["result"]
