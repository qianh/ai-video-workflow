from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.drafts import DraftService
from workflow_sidecar.persistence.generation import GenerationService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_plan_execute_review_never_auto_formal(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Gen")
    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    gen = GenerationService(db)
    drafts = DraftService(db)

    run_rec = gen.create_run(
        title="E01 生成",
        schema_id="episode_outline_v1",
        intent={"constraints": ["竖屏", "悬疑"]},
        target_type="episode_outline",
        target_id="ep-1",
        branch_id=story.primary_branch_id(),
    )
    assert run_rec["status"] == "created"

    planned = gen.plan(run_rec["id"])
    assert planned["run"]["status"] == "planned"

    executed = gen.execute(
        run_rec["id"],
        output={
            "episode_no": 1,
            "title": "夜市",
            "summary": "发现 U 盘",
            "hooks": ["发光"],
        },
    )
    assert executed["schema_ok"] is True
    assert executed["draft_id"]
    assert executed["run"]["status"] == "reviewing"

    reviewed = gen.review(
        run_rec["id"],
        verdict="pass",
        findings=[{"category": "structure", "severity": "info", "message": "ok"}],
    )
    assert reviewed["verdict"] == "pass"
    assert reviewed["formal_revision_created"] is False
    assert reviewed["run"]["status"] == "approved"

    # Still no formal revision until explicit draft.promote
    assert drafts.list_revisions() == []

    gate = gen.open_draft_gate(run_rec["id"])
    assert gate["can_promote"] is True
    formal = drafts.promote(
        gate["draft"]["id"],
        primary_branch_id=story.primary_branch_id(),
    )
    assert formal["status"] == "approved"
    assert len(drafts.list_revisions()) == 1

    history = gen.get_history(run_rec["id"])
    assert len(history["plans"]) == 1
    assert len(history["executions"]) == 1
    assert len(history["reviews"]) == 1
    service.close()


def test_review_revise_and_human_accept(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "Gen2")
    gen = GenerationService(service.require_project_db())
    story = StoryService(service.require_project_db(), Path(project.root_path))
    run_rec = gen.create_run(
        title="角色生成",
        schema_id="character_bible_v1",
        intent={},
        target_type="character_bible",
        branch_id=story.primary_branch_id(),
    )
    gen.plan(run_rec["id"])
    gen.execute(
        run_rec["id"],
        output={"name": "阿宁", "role": "主角", "traits": ["冷静"]},
    )
    revised = gen.review(run_rec["id"], verdict="revise", findings=[{"msg": "补背景"}])
    assert revised["run"]["status"] == "needs_revision"

    # New iteration
    gen.plan(run_rec["id"])
    gen.execute(
        run_rec["id"],
        output={"name": "阿宁", "role": "主角", "traits": ["冷静", "好奇"]},
    )
    human = gen.review(run_rec["id"], verdict="human_review", findings=[])
    assert human["run"]["status"] == "needs_human"
    with pytest.raises(ValueError, match="draft gate opens only after"):
        gen.open_draft_gate(run_rec["id"])
    accepted = gen.accept_human_review(run_rec["id"], reason="人工确认可用")
    assert accepted["status"] == "approved"
    assert gen.open_draft_gate(run_rec["id"])["can_promote"] is True
    service.close()


def test_generation_rpc_pipeline(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "GenRPC"},
            )
        )
        await runtime.handle(
            Request(
                "create",
                "generation.create",
                {
                    "title": "E01",
                    "schema_id": "episode_outline_v1",
                    "intent": {"constraints": ["悬疑"]},
                },
            )
        )
        run_id = next(m for m in messages if m["id"] == "create")["result"]["id"]
        await runtime.handle(
            Request("plan", "generation.plan", {"run_id": run_id})
        )
        await runtime.handle(
            Request(
                "exec",
                "generation.execute",
                {
                    "run_id": run_id,
                    "output": {
                        "episode_no": 1,
                        "title": "开端",
                        "summary": "发现",
                        "hooks": ["钩子"],
                    },
                },
            )
        )
        await runtime.handle(
            Request(
                "rev",
                "generation.review",
                {"run_id": run_id, "verdict": "pass", "findings": []},
            )
        )
        await runtime.handle(
            Request("gate", "generation.open_draft_gate", {"run_id": run_id})
        )
        await runtime.handle(Request("hist", "generation.history", {"run_id": run_id}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["plan"]["result"]["run"]["status"] == "planned"
    assert by_id["exec"]["result"]["schema_ok"] is True
    assert by_id["rev"]["result"]["formal_revision_created"] is False
    assert by_id["gate"]["result"]["can_promote"] is True
    assert by_id["hist"]["result"]["reviews"][0]["verdict"] == "pass"
