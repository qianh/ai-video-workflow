from __future__ import annotations

import asyncio
from pathlib import Path

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.jobs import JobQueue
from workflow_sidecar.persistence.production import ProductionService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.persistence.storyboard import StoryboardService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_job_retry(tmp_path: Path) -> None:
    ws = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    ws.create_project(parent, "Jobs")
    q = JobQueue(ws.require_project_db())
    job = q.enqueue(kind="demo.ping", payload={"n": 1})
    claimed = q.claim("w1")
    assert claimed is not None
    q.fail(claimed.id, "w1", error="boom", retry=False)
    retried = q.retry(claimed.id)
    assert retried.status == "queued"
    ws.close()


def test_production_execute_rpc(tmp_path: Path) -> None:
    async def scenario() -> dict[str, dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(messages.append, global_db_path=tmp_path / "g.db")
        parent = tmp_path / "p"
        parent.mkdir()
        await runtime.handle(
            Request("c", "project.create", {"parent_dir": str(parent), "name": "Ex"})
        )
        db = runtime._workspace.require_project_db()
        root = Path(runtime._workspace.current.root_path)  # type: ignore[union-attr]
        branch = StoryService(db, root).primary_branch_id()
        sb = StoryboardService(db)
        board = sb.create_storyboard(episode_id="e1", branch_id=branch)
        rev = board["current_revision"]["id"]
        gen = sb.generate_default_shots(rev, count=6, branch_id=branch)
        srev = gen["shots"][0]["current_revision"]["id"]
        prod = ProductionService(db, root)
        planned = prod.plan_shot_item(srev, kind="image")
        await runtime.handle(
            Request("ex", "production.execute", {"item_id": planned["id"]})
        )
        await runtime.handle(
            Request("jr", "job.enqueue", {"kind": "demo.x", "payload": {}})
        )
        # fail then retry via RPC needs claim - just list
        await runtime.handle(Request("jl", "job.list", {"limit": 10}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["ex"]["result"]["status"] == "succeeded"
    assert by_id["jl"]["result"]["jobs"]
