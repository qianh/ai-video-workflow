from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.locations import LocationService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_location_pack_prop_spatial_flow(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "World")
    assert project.schema_version >= 11

    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    locs = LocationService(db)
    branch = story.primary_branch_id()

    market = locs.create_location(
        branch_id=branch,
        name="夜市东口",
        location_type="exterior",
        description="雨夜摊位区",
        is_core=True,
    )
    market = locs.approve_location_revision(market["current_revision"]["id"])
    assert market["is_core"] is True

    alley = locs.create_location(
        branch_id=branch,
        name="后巷",
        location_type="exterior",
        description="狭窄后巷",
    )
    alley = locs.approve_location_revision(alley["current_revision"]["id"])

    link = locs.add_spatial_link(
        branch_id=branch,
        source_location_id=market["id"],
        target_location_id=alley["id"],
        link_type="connected",
        description="东口通往后巷",
    )
    assert link["bidirectional"] is True

    prop = locs.create_prop(
        branch_id=branch,
        name="发光 U 盘",
        appearance="半透明，冷蓝光",
        state_notes="首次出现在夜市",
    )
    prop = locs.approve_prop_revision(prop["current_revision"]["id"])
    assert prop["current_revision"]["status"] == "approved"

    pack = locs.create_pack(
        location_id=market["id"],
        layout={"zones": ["stalls", "entrance"]},
        direction_axis="east-west",
        primary_view="from entrance looking east",
        camera_angles=["wide establishing", "medium stall"],
        entrances=[{"id": "main", "side": "west"}],
        day_variant={"light": "overcast neon"},
        night_variant={"light": "rain neon", "wet_ground": True},
    )
    rev_id = pack["current_revision"]["id"]
    locs.anchor_prop(
        location_pack_revision_id=rev_id,
        prop_id=prop["id"],
        anchor_label="stall_floor",
        position={"x": 1.2, "y": 0, "z": 0.4},
    )

    gate0 = locs.production_gate(market["id"])
    assert gate0["location_pack_required"] is True
    assert gate0["ready_for_production"] is False

    validated = locs.validate_pack(rev_id)
    assert validated["valid"] is True
    confirmed = locs.confirm_pack(rev_id)
    assert confirmed["confirmed_revision"]["status"] == "confirmed"
    assert len(confirmed["confirmed_revision"]["prop_anchors"]) == 1

    gate1 = locs.production_gate(market["id"])
    assert gate1["ready_for_production"] is True

    overview = locs.world_overview(branch)
    assert len(overview["locations"]) == 2
    assert len(overview["spatial_links"]) == 1
    assert len(overview["props"]) == 1
    service.close()


def test_location_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "LocRPC"},
            )
        )
        await runtime.handle(
            Request(
                "loc",
                "location.create",
                {
                    "name": "夜市东口",
                    "location_type": "exterior",
                    "is_core": True,
                },
            )
        )
        loc = next(m for m in messages if m["id"] == "loc")["result"]
        await runtime.handle(
            Request(
                "loc_ok",
                "location.approve",
                {"revision_id": loc["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "loc2",
                "location.create",
                {"name": "后巷", "location_type": "exterior"},
            )
        )
        loc2 = next(m for m in messages if m["id"] == "loc2")["result"]
        await runtime.handle(
            Request(
                "loc2_ok",
                "location.approve",
                {"revision_id": loc2["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "link",
                "spatial.add_link",
                {
                    "source_location_id": loc["id"],
                    "target_location_id": loc2["id"],
                    "link_type": "connected",
                },
            )
        )
        await runtime.handle(
            Request(
                "prop",
                "prop.create",
                {"name": "发光 U 盘", "appearance": "冷蓝光"},
            )
        )
        prop = next(m for m in messages if m["id"] == "prop")["result"]
        await runtime.handle(
            Request(
                "prop_ok",
                "prop.approve",
                {"revision_id": prop["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "pack",
                "location.create_pack",
                {
                    "location_id": loc["id"],
                    "layout": {"zones": ["stalls"]},
                    "direction_axis": "E-W",
                    "primary_view": "entrance",
                    "camera_angles": ["wide"],
                    "night_variant": {"rain": True},
                },
            )
        )
        pack = next(m for m in messages if m["id"] == "pack")["result"]
        rev = pack["current_revision"]["id"]
        await runtime.handle(
            Request(
                "anchor",
                "location.anchor_prop",
                {
                    "revision_id": rev,
                    "prop_id": prop["id"],
                    "anchor_label": "ground",
                    "position": {"x": 0, "y": 0},
                },
            )
        )
        await runtime.handle(
            Request("val", "location.validate_pack", {"revision_id": rev})
        )
        await runtime.handle(
            Request("ok", "location.confirm_pack", {"revision_id": rev})
        )
        await runtime.handle(
            Request("gate", "location.gate", {"location_id": loc["id"]})
        )
        await runtime.handle(Request("ov", "location.overview", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["loc_ok"]["result"]["is_core"] is True
    assert by_id["link"]["result"]["link_type"] == "connected"
    assert by_id["val"]["result"]["valid"] is True
    assert by_id["ok"]["result"]["confirmed_revision"]["status"] == "confirmed"
    assert by_id["gate"]["result"]["ready_for_production"] is True
    assert by_id["ov"]["result"]["props"]


def test_location_errors(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "LocErr")
    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    locs = LocationService(db)
    branch = story.primary_branch_id()

    with pytest.raises(ValueError, match="name"):
        locs.create_location(branch_id=branch, name=" ")
    with pytest.raises(ValueError, match="location_type"):
        locs.create_location(branch_id=branch, name="X", location_type="space")
    with pytest.raises(ValueError, match="not found"):
        locs.get_location("missing")

    loc = locs.create_location(branch_id=branch, name="A", is_core=True)
    locs.approve_location_revision(loc["current_revision"]["id"])
    pack = locs.create_pack(location_id=loc["id"])
    rev = pack["current_revision"]["id"]
    rejected = locs.validate_pack(rev)
    assert rejected["valid"] is False

    with pytest.raises(ValueError, match="must differ"):
        locs.add_spatial_link(
            branch_id=branch,
            source_location_id=loc["id"],
            target_location_id=loc["id"],
            link_type="adjacent",
        )
    with pytest.raises(ValueError, match="appearance"):
        locs.create_prop(branch_id=branch, name="P", appearance=" ")

    gate = locs.production_gate(loc["id"])
    assert gate["ready_for_production"] is False

    # cover update_pack / mark_core / list helpers
    locs.mark_core(loc["id"], is_core=False)
    assert locs.get_location(loc["id"])["is_core"] is False
    locs.mark_core(loc["id"], is_core=True)
    filled = locs.update_pack_revision(
        rev,
        layout={"zones": ["a"]},
        direction_axis="N-S",
        primary_view="door",
        camera_angles=["wide"],
        night_variant={"rain": True},
        entrances=[{"id": "e1"}],
        furniture_anchors=[{"id": "table"}],
        day_variant={"sun": True},
        reference_asset_ids=["ref-1"],
        notes="updated",
    )
    assert filled["primary_view"] == "door"
    assert locs.list_locations(branch_id=branch)
    assert locs.list_packs(location_id=loc["id"])
    assert locs.list_props(branch_id=branch) == []
    assert locs.list_spatial_links(branch_id=branch) == []
    with pytest.raises(ValueError, match="not found"):
        locs.get_pack("missing")
    with pytest.raises(ValueError, match="not found"):
        locs.get_prop("missing")
    with pytest.raises(ValueError, match="not found"):
        locs.get_spatial_link("missing")
    service.close()
