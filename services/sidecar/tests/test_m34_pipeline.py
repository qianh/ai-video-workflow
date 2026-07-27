from __future__ import annotations

import asyncio
from pathlib import Path

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.awap import AwapService
from workflow_sidecar.persistence.gates import GateService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_awap_probe_and_budget(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "AWAP")
    assert project.schema_version >= 16
    awap = AwapService(service.require_project_db())
    catalog = awap.catalog()
    assert catalog["capabilities"]
    assert catalog["adapters"]
    probes = awap.probe()
    assert probes["probes"]
    route = awap.route(capability="ffmpeg.transcode")
    assert route["allowed"] is True
    service.close()


def test_full_m34_pipeline_bootstrap(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Pipe")
    db = service.require_project_db()
    root = Path(project.root_path)
    story = StoryService(db, root)
    gates = GateService(db, root)
    branch = story.primary_branch_id()

    result = gates.bootstrap_pipeline(branch_id=branch)
    assert result["bootstrap"] == "trial_m2_m3_m4"
    assert result["shot_count"] == 18
    assert result["production_items"] == 18
    assert result["timeline_duration_ms"] > 0
    assert result["ready_for_export"] is True
    assert len(result["exports"]) == 3
    profiles = {e["profile"] for e in result["exports"]}
    assert profiles == {"master", "douyin", "hongguo"}
    assert (root / result["ass_path"]).is_file()
    assert (root / result["proxy_render"]).is_file()
    service.close()


def test_stale_propagation_and_qc_review(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "Stale")
    db = service.require_project_db()
    root = Path(project.root_path)
    from workflow_sidecar.persistence.production import ProductionService
    from workflow_sidecar.persistence.storyboard import StoryboardService

    story = StoryService(db, root)
    branch = story.primary_branch_id()
    sb_svc = StoryboardService(db)
    prod = ProductionService(db, root)
    sb = sb_svc.create_storyboard(episode_id="ep-x", branch_id=branch)
    rev = sb["current_revision"]["id"]
    gen = sb_svc.generate_default_shots(rev, count=6, branch_id=branch)
    batch = prod.batch_plan_and_execute(rev, kind="image")
    assert batch["count"] == 6
    first = batch["items"][0]
    prod.lock_item(first["id"], locked=True)
    shot_rev = gen["shots"][1]["current_revision"]["id"]
    stale = prod.mark_upstream_changed(
        upstream_type="shot_revision", upstream_id=shot_rev
    )
    assert stale["count"] >= 1
    reviews = prod.list_review_queue()
    assert reviews
    resolved = prod.resolve_review(
        reviews[0]["id"], status="waived", note="mock ok for trial"
    )
    assert resolved["status"] == "waived"
    service.close()


def test_pipeline_rpc(tmp_path: Path) -> None:
    async def scenario() -> dict[str, dict[str, object]]:
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
                {"parent_dir": str(parent), "name": "PipeRPC"},
            )
        )
        await runtime.handle(Request("awap", "awap.catalog", {}))
        await runtime.handle(Request("probe", "awap.probe", {}))
        await runtime.handle(Request("pipe", "trial.bootstrap_pipeline", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["awap"]["result"]["protocol"].startswith("AWAP")
    assert by_id["pipe"]["result"]["ready_for_export"] is True
    assert by_id["pipe"]["result"]["shot_count"] >= 18
