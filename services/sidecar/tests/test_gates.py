from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.gates import GateService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.persistence.story_package import StoryPackageService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_trial_bootstrap_confirms_m2_gates(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Trial")
    assert project.schema_version >= 16

    db = service.require_project_db()
    root = Path(project.root_path)
    story = StoryService(db, root)
    gates = GateService(db, root)
    branch = story.primary_branch_id()

    before = gates.status(branch_id=branch)
    assert before["ready_for_batch_production"] is False
    assert before["gates"]["story_package"]["blockers"]

    result = gates.bootstrap_trial(branch_id=branch)
    assert result["ready_for_batch_production"] is True
    assert result["gates"]["story_package"]["status"] == "confirmed"
    assert result["gates"]["identity_and_locations"]["status"] == "confirmed"
    assert result["gates"]["story_package"]["valid"] is True
    assert result["gates"]["identity_and_locations"]["valid"] is True

    # Changing story package targets invalidates the gate.
    packages = StoryPackageService(db)
    episodes = packages.list_episodes(branch)
    # Add another timeline beat and new package revision.
    beat = packages.add_timeline_beat(
        branch_id=branch,
        beat_no=99,
        title="终局",
        summary="对质",
        episode_nos=[3],
    )
    rules = packages.list_world_rules(branch)
    new_pkg = packages.create_package_revision(
        branch_id=branch,
        name="试验项目故事包 v2",
        positioning={"theme": "都市悬疑·升级", "audience": "短剧"},
        world_rule_ids=[r["id"] for r in rules],
        timeline_beat_ids=[beat["id"]],
        episode_ids=[e["id"] for e in episodes],
        claims_for_rules=["雨夜"],
    )
    packages.approve_package_revision(new_pkg["id"])

    refreshed = gates.status(branch_id=branch)
    story_gate = refreshed["gates"]["story_package"]
    # After change: either invalidated previous and pending new, not ready.
    assert refreshed["ready_for_batch_production"] is False
    assert story_gate["status"] in {"pending", "invalidated"} or not story_gate.get(
        "valid", True
    )

    # Re-confirm after re-evaluate.
    story_eval = gates.evaluate(branch_id=branch, gate_type="story_package")
    if story_eval["status"] == "pending" and story_eval["ready"]:
        gates.confirm(story_eval["id"], confirmation_note="reconfirm after change")
    final = gates.status(branch_id=branch)
    # identity gate still valid; story reconfirmed if ready
    assert final["gates"]["identity_and_locations"]["valid"] is True
    service.close()


def test_gate_rpc_bootstrap(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "GateRPC"},
            )
        )
        await runtime.handle(Request("boot", "trial.bootstrap", {}))
        await runtime.handle(Request("st", "gate.status", {}))
        await runtime.handle(Request("lst", "gate.list", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["boot"]["result"]["ready_for_batch_production"] is True
    assert by_id["st"]["result"]["ready_for_batch_production"] is True
    assert by_id["lst"]["result"]["gates"]


def test_gate_blocks_incomplete(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "GateBlock")
    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    gates = GateService(db, Path(project.root_path))
    branch = story.primary_branch_id()

    story_gate = gates.evaluate(branch_id=branch, gate_type="story_package")
    assert story_gate["ready"] is False
    assert story_gate["blockers"]
    with pytest.raises(ValueError, match="not ready"):
        gates.confirm(story_gate["id"])

    looks = gates.evaluate(branch_id=branch, gate_type="identity_and_locations")
    assert looks["ready"] is False
    codes = {b["code"] for b in looks["blockers"]}
    assert "missing_main_character" in codes or "missing_core_location" in codes
    service.close()
