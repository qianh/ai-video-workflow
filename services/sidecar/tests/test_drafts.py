from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.drafts import DraftService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_draft_validate_and_promote_gate(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Drafts")
    drafts = DraftService(service.require_project_db())
    story = StoryService(service.require_project_db(), Path(project.root_path))
    primary = story.primary_branch_id()

    incomplete = drafts.create(
        schema_id="episode_outline_v1",
        title="E01 草稿",
        target_type="episode_outline",
        target_id="ep-1",
        branch_id=primary,
        payload={"episode_no": 1, "title": "开端"},
    )
    rejected = drafts.validate(incomplete["id"])
    assert rejected["status"] == "rejected"
    assert rejected["validation_errors"]

    with pytest.raises(ValueError, match="validated"):
        drafts.promote(incomplete["id"], primary_branch_id=primary)

    fixed = drafts.update(
        incomplete["id"],
        payload={
            "episode_no": 1,
            "title": "开端",
            "summary": "雨夜发现 U 盘",
            "hooks": ["发光 U 盘"],
        },
    )
    assert fixed["status"] == "draft"
    validated = drafts.validate(fixed["id"])
    assert validated["status"] == "validated"

    formal = drafts.promote(fixed["id"], primary_branch_id=primary)
    assert formal["status"] == "approved"
    assert formal["revision_no"] == 1
    assert formal["draft_id"] == fixed["id"]
    assert drafts.get(fixed["id"])["status"] == "promoted"

    # Cannot promote without draft gate: no direct formal create API exists.
    with pytest.raises(ValueError, match="promoted"):
        drafts.validate(fixed["id"])

    service.close()


def test_draft_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "DraftRPC"},
            )
        )
        await runtime.handle(Request("schemas", "draft.list_schemas", {}))
        await runtime.handle(
            Request(
                "d",
                "draft.create",
                {
                    "schema_id": "character_bible_v1",
                    "title": "女主",
                    "target_type": "character_bible",
                    "target_id": "char-1",
                    "payload": {
                        "name": "阿宁",
                        "role": "protagonist",
                        "traits": ["冷静", "好奇"],
                    },
                },
            )
        )
        draft_id = next(m for m in messages if m["id"] == "d")["result"]["id"]
        await runtime.handle(
            Request("v", "draft.validate", {"draft_id": draft_id})
        )
        await runtime.handle(
            Request("p", "draft.promote", {"draft_id": draft_id})
        )
        await runtime.handle(Request("revs", "revision.list", {}))
        await runtime.handle(Request("list", "draft.list", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["schemas"]["result"]["schemas"]
    assert by_id["v"]["result"]["status"] == "validated"
    assert by_id["p"]["result"]["status"] == "approved"
    assert by_id["revs"]["result"]["revisions"][0]["revision_no"] == 1
    assert by_id["list"]["result"]["drafts"][0]["status"] == "promoted"
