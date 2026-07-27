from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_single_primary_and_fork_copies_events(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Branches")
    story = StoryService(service.require_project_db(), Path(project.root_path))

    branches = story.list_branches()
    assert len(branches) == 1
    assert branches[0]["is_primary"] is True
    primary_id = branches[0]["id"]

    story.create_event(
        title="起点",
        summary="主线事件",
        order_key=1,
        origin="creative",
    )
    forked = story.fork_branch(from_branch_id=primary_id, name="探索线 A")
    assert forked["is_primary"] is False
    assert forked["status"] == "exploring"
    assert forked["parent_branch_id"] == primary_id
    assert forked["copied_events"] == 1

    forked_events = story.list_events(forked["id"])
    primary_events = story.list_events(primary_id)
    assert len(forked_events) == 1
    assert len(primary_events) == 1
    assert forked_events[0].event_id != primary_events[0].event_id
    assert forked_events[0].title == "起点"

    promoted = story.set_primary(forked["id"])
    assert promoted["is_primary"] is True
    assert promoted["status"] == "primary"
    old = story.get_branch(primary_id)
    assert old["is_primary"] is False
    assert old["status"] == "candidate"

    primaries = [b for b in story.list_branches() if b["is_primary"]]
    assert len(primaries) == 1

    try:
        story.archive_branch(promoted["id"])
        raise AssertionError("expected archive primary to fail")
    except ValueError as exc:
        assert "primary" in str(exc)

    archived = story.archive_branch(primary_id)
    assert archived["status"] == "archived"

    exploring = story.create_branch(name="草稿线", status="exploring")
    assert exploring["is_primary"] is False
    assert exploring["status"] == "exploring"
    with pytest.raises(ValueError, match="set_primary"):
        story.create_branch(name="坏主线", status="primary")
    with pytest.raises(ValueError, match="archived"):
        story.fork_branch(from_branch_id=primary_id, name="不可叉")
    with pytest.raises(ValueError, match="not found"):
        story.get_branch("missing-branch")
    service.close()


def test_branch_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "BR"},
            )
        )
        await runtime.handle(Request("list1", "story.list_branches", {}))
        primary = next(m for m in messages if m["id"] == "list1")["result"][
            "branches"
        ][0]["id"]
        await runtime.handle(
            Request(
                "evt",
                "story.create_event",
                {
                    "title": "A",
                    "summary": "main",
                    "order_key": 1,
                    "origin": "creative",
                },
            )
        )
        await runtime.handle(
            Request(
                "fork",
                "story.fork_branch",
                {"from_branch_id": primary, "name": "探索"},
            )
        )
        forked_id = next(m for m in messages if m["id"] == "fork")["result"]["id"]
        await runtime.handle(
            Request("set", "story.set_primary", {"branch_id": forked_id})
        )
        await runtime.handle(Request("list2", "story.list_branches", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["fork"]["result"]["copied_events"] == 1
    assert by_id["set"]["result"]["is_primary"] is True
    primaries = [
        b for b in by_id["list2"]["result"]["branches"] if b["is_primary"]
    ]
    assert len(primaries) == 1
    assert primaries[0]["name"] == "探索"
