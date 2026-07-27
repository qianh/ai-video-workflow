from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.continuity import ContinuityService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_continuity_ledger_conflicts_and_snapshot(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Cont")
    assert project.schema_version >= 14

    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    cont = ContinuityService(db)
    branch = story.primary_branch_id()
    char_id = "char-aning"

    outfit_e1 = cont.add_state(
        branch_id=branch,
        subject_type="character",
        subject_id=char_id,
        state_key="outfit",
        value={"item": "raincoat"},
        story_time_from="E01",
        time_from_ord=100,
        story_time_to="E02",
        time_to_ord=200,
        priority=0,
        source_type="script",
    )
    assert outfit_e1["status"] == "active"

    # Non-overlapping later state OK.
    cont.add_state(
        branch_id=branch,
        subject_type="character",
        subject_id=char_id,
        state_key="outfit",
        value={"item": "hoodie"},
        story_time_from="E02",
        time_from_ord=200,
        priority=0,
    )

    # Equal-priority overlap is blocked.
    with pytest.raises(ValueError, match="equal-priority"):
        cont.add_state(
            branch_id=branch,
            subject_type="character",
            subject_id=char_id,
            state_key="outfit",
            value={"item": "jacket"},
            story_time_from="E01.5",
            time_from_ord=150,
            time_to_ord=180,
            priority=0,
        )

    # Higher priority overlap allowed; warning in check.
    cont.add_state(
        branch_id=branch,
        subject_type="character",
        subject_id=char_id,
        state_key="outfit",
        value={"item": "hospital gown"},
        story_time_from="E01.injury",
        time_from_ord=120,
        time_to_ord=160,
        priority=10,
        source_type="user",
    )

    injury = cont.add_state(
        branch_id=branch,
        subject_type="character",
        subject_id=char_id,
        state_key="injury",
        value={"part": "left arm", "severity": "bruise"},
        story_time_from="E01",
        time_from_ord=100,
        priority=0,
    )
    cont.end_state(injury["id"], story_time_to="E03", time_to_ord=300)

    prop = cont.add_state(
        branch_id=branch,
        subject_type="prop",
        subject_id="prop-usb",
        state_key="owner",
        value={"character_id": char_id},
        story_time_from="E01",
        time_from_ord=100,
        priority=0,
    )
    assert prop["value"]["character_id"] == char_id

    effective = cont.effective_at(
        branch_id=branch,
        subject_type="character",
        subject_id=char_id,
        state_key="outfit",
        at_time_ord=130,
    )
    assert effective is not None
    assert effective["value"]["item"] == "hospital gown"

    at_e2 = cont.effective_at(
        branch_id=branch,
        subject_type="character",
        subject_id=char_id,
        state_key="outfit",
        at_time_ord=200,
    )
    assert at_e2 is not None
    assert at_e2["value"]["item"] == "hoodie"

    report = cont.check_conflicts(branch_id=branch, persist=True)
    assert report["blocked"] is False
    assert report["warning_count"] >= 1

    snap = cont.create_snapshot(
        branch_id=branch,
        at_story_time="E01.mid",
        at_time_ord=130,
        purpose="shot preflight",
    )
    assert snap["immutable"] is True
    assert any(s["state_key"] == "outfit" for s in snap["states"])
    assert any(s["value"].get("item") == "hospital gown" for s in snap["states"])

    # Force equal-priority conflict then snapshot blocked.
    cont.add_state(
        branch_id=branch,
        subject_type="character",
        subject_id=char_id,
        state_key="injury",
        value={"part": "head"},
        story_time_from="E01.b",
        time_from_ord=110,
        time_to_ord=140,
        priority=0,
        allow_equal_priority_overlap=True,
    )
    blocked = cont.check_conflicts(branch_id=branch)
    assert blocked["blocked"] is True
    with pytest.raises(ValueError, match="cannot snapshot"):
        cont.create_snapshot(
            branch_id=branch, at_story_time="E01.x", at_time_ord=120
        )

    overview = cont.ledger_overview(branch)
    assert overview["active_state_count"] >= 4
    assert cont.list_conflict_reports(branch_id=branch)
    assert cont.list_snapshots(branch_id=branch)
    assert cont.list_states(
        branch_id=branch, subject_type="character", subject_id=char_id
    )
    all_eff = cont.resolve_all_effective(branch_id=branch, at_time_ord=130)
    assert all_eff
    service.close()


def test_continuity_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "ContRPC"},
            )
        )
        await runtime.handle(
            Request(
                "a",
                "continuity.add",
                {
                    "subject_type": "character",
                    "subject_id": "c1",
                    "state_key": "outfit",
                    "value": {"item": "raincoat"},
                    "story_time_from": "E01",
                    "time_from_ord": 10,
                    "story_time_to": "E02",
                    "time_to_ord": 20,
                },
            )
        )
        await runtime.handle(
            Request(
                "b",
                "continuity.add",
                {
                    "subject_type": "prop",
                    "subject_id": "p1",
                    "state_key": "owner",
                    "value": {"character_id": "c1"},
                    "story_time_from": "E01",
                    "time_from_ord": 10,
                },
            )
        )
        await runtime.handle(
            Request(
                "eff",
                "continuity.effective",
                {
                    "subject_type": "character",
                    "subject_id": "c1",
                    "state_key": "outfit",
                    "at_time_ord": 15,
                },
            )
        )
        await runtime.handle(Request("chk", "continuity.check", {}))
        await runtime.handle(
            Request(
                "snap",
                "continuity.snapshot",
                {
                    "at_story_time": "E01.mid",
                    "at_time_ord": 15,
                    "purpose": "test",
                },
            )
        )
        await runtime.handle(Request("ov", "continuity.overview", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["a"]["result"]["state_key"] == "outfit"
    assert by_id["eff"]["result"]["state"]["value"]["item"] == "raincoat"
    assert by_id["chk"]["result"]["blocked"] is False
    assert by_id["snap"]["result"]["immutable"] is True
    assert by_id["ov"]["result"]["active_state_count"] >= 2


def test_continuity_errors(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "ContErr")
    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    cont = ContinuityService(db)
    branch = story.primary_branch_id()

    with pytest.raises(ValueError, match="subject_type"):
        cont.add_state(
            branch_id=branch,
            subject_type="vehicle",
            subject_id="x",
            state_key="k",
            value=1,
            story_time_from="E01",
            time_from_ord=1,
        )
    with pytest.raises(ValueError, match="time_to_ord"):
        cont.add_state(
            branch_id=branch,
            subject_type="character",
            subject_id="x",
            state_key="k",
            value=1,
            story_time_from="E01",
            time_from_ord=10,
            time_to_ord=5,
        )
    with pytest.raises(ValueError, match="not found"):
        cont.get_state("missing")
    with pytest.raises(ValueError, match="not found"):
        cont.get_snapshot("missing")

    s = cont.add_state(
        branch_id=branch,
        subject_type="location",
        subject_id="loc-1",
        state_key="open_closed",
        value={"open": True},
        story_time_from="E01",
        time_from_ord=1,
    )
    cont.supersede_state(s["id"], reason="retcon")
    assert cont.get_state(s["id"])["status"] == "superseded"
    with pytest.raises(ValueError, match="cannot end"):
        cont.end_state(s["id"], story_time_to="E02", time_to_ord=2)
    service.close()
