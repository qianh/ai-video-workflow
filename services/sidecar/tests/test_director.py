from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.director import DirectorService, merge_with_locks
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_merge_locks_three_levels() -> None:
    layers = [
        {
            "id": "p",
            "scope_level": "project",
            "payload": {
                "style_name": "modern donghua",
                "palette": {"primary": "teal"},
                "line_weight": "medium",
            },
            "locked_fields": ["style_name"],
        },
        {
            "id": "e",
            "scope_level": "episode",
            "payload": {
                "style_name": "should-not-win",
                "palette": {"accent": "amber"},
                "mood": "rainy",
            },
            "locked_fields": [],
        },
        {
            "id": "s",
            "scope_level": "shot",
            "payload": {"mood": "tense", "framing_bias": "closeup"},
            "locked_fields": [],
        },
    ]
    merged = merge_with_locks(layers)
    assert merged["effective"]["style_name"] == "modern donghua"
    assert merged["effective"]["palette"] == {"primary": "teal", "accent": "amber"}
    assert merged["effective"]["mood"] == "tense"
    assert merged["provenance"]["style_name"]["scope_level"] == "project"
    assert merged["provenance"]["mood"]["scope_level"] == "shot"
    assert "style_name" in merged["locked_fields"]


def test_visual_and_director_inheritance(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Dir")
    assert project.schema_version >= 13

    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    director = DirectorService(db)
    branch = story.primary_branch_id()

    bible = director.create_visual_bible(
        branch_id=branch,
        name="试播视觉圣经",
        style_name="现代国漫半写实",
        payload={
            "character_proportion": "1:7",
            "line_work": "clean",
            "palette": {"primary": "cold teal"},
            "forbidden": ["photoreal skin"],
        },
        locked_fields=["style_name", "forbidden"],
        scope_level="project",
    )
    proj_rev = bible["current_revision"]["id"]
    approved = director.approve_visual_revision(proj_rev)
    assert approved["current_revision_id"] == proj_rev
    assert approved["approved_revision"]["status"] == "approved"

    ep_rev = director.add_visual_revision(
        bible_id=bible["id"],
        scope_level="episode",
        scope_ref="E01",
        payload={
            "style_name": "attempt override",
            "palette": {"accent": "neon magenta"},
            "lighting": "wet neon night",
        },
    )
    director.approve_visual_revision(ep_rev["id"])

    shot_rev = director.add_visual_revision(
        bible_id=bible["id"],
        scope_level="shot",
        scope_ref="E01-S03",
        payload={"lighting": "silhouette", "camera_feel": "handheld"},
    )
    director.approve_visual_revision(shot_rev["id"])

    resolved = director.resolve_visual(
        bible_id=bible["id"],
        episode_ref="E01",
        shot_ref="E01-S03",
    )
    assert resolved["effective"]["style_name"] == "现代国漫半写实"
    assert resolved["effective"]["forbidden"] == ["photoreal skin"]
    assert resolved["effective"]["palette"]["primary"] == "cold teal"
    assert resolved["effective"]["palette"]["accent"] == "neon magenta"
    assert resolved["effective"]["lighting"] == "silhouette"
    assert resolved["provenance"]["style_name"]["scope_level"] == "project"
    assert resolved["provenance"]["lighting"]["scope_level"] == "shot"

    # Project re-approve creates impact report for child scopes.
    again = director.add_visual_revision(
        bible_id=bible["id"],
        scope_level="project",
        style_name="现代国漫半写实",
        payload={
            "character_proportion": "1:7.5",
            "line_work": "clean",
            "palette": {"primary": "cold teal"},
            "forbidden": ["photoreal skin"],
        },
        locked_fields=["style_name", "forbidden"],
    )
    impact = director.approve_visual_revision(again["id"])
    assert impact["impact_report"]["affected_revision_ids"]
    assert "child scope" in impact["impact_report"]["summary"]

    preset = director.create_director_preset(
        branch_id=branch,
        name="竖屏悬疑预设",
        payload={
            "shot_duration_ms": {"min": 1200, "max": 3500},
            "framing_mix": {"CU": 0.3, "MS": 0.5, "WS": 0.2},
            "motion_intensity": "low",
            "forbidden_moves": ["whip pan"],
        },
        locked_fields=["forbidden_moves"],
        scope_level="project",
    )
    director.approve_director_revision(preset["current_revision"]["id"])
    ep_dir = director.add_director_revision(
        preset_id=preset["id"],
        scope_level="episode",
        scope_ref="E01",
        payload={
            "forbidden_moves": ["should-not-apply"],
            "motion_intensity": "medium",
            "transition_rate": "sparse",
        },
    )
    director.approve_director_revision(ep_dir["id"])
    shot_dir = director.add_director_revision(
        preset_id=preset["id"],
        scope_level="shot",
        scope_ref="E01-S03",
        payload={"motion_intensity": "hold", "dialogue_coverage": "OTS"},
    )
    director.approve_director_revision(shot_dir["id"])

    dres = director.resolve_director(
        preset_id=preset["id"],
        episode_ref="E01",
        shot_ref="E01-S03",
    )
    assert dres["effective"]["forbidden_moves"] == ["whip pan"]
    assert dres["effective"]["motion_intensity"] == "hold"
    assert dres["effective"]["dialogue_coverage"] == "OTS"
    assert dres["provenance"]["forbidden_moves"]["scope_level"] == "project"

    ov = director.overview(branch)
    assert len(ov["visual_bibles"]) == 1
    assert len(ov["director_presets"]) == 1
    service.close()


def test_director_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "DirRPC"},
            )
        )
        await runtime.handle(
            Request(
                "vb",
                "visual.create",
                {
                    "name": "圣经",
                    "style_name": "donghua",
                    "payload": {"line_work": "ink"},
                    "locked_fields": ["style_name"],
                },
            )
        )
        bible = next(m for m in messages if m["id"] == "vb")["result"]
        await runtime.handle(
            Request(
                "vba",
                "visual.approve",
                {"revision_id": bible["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "vbe",
                "visual.add_revision",
                {
                    "bible_id": bible["id"],
                    "scope_level": "episode",
                    "scope_ref": "E01",
                    "payload": {"mood": "rain"},
                },
            )
        )
        ep = next(m for m in messages if m["id"] == "vbe")["result"]
        await runtime.handle(
            Request("vbea", "visual.approve", {"revision_id": ep["id"]})
        )
        await runtime.handle(
            Request(
                "vr",
                "visual.resolve",
                {"bible_id": bible["id"], "episode_ref": "E01"},
            )
        )
        await runtime.handle(
            Request(
                "dp",
                "director.create",
                {
                    "name": "默认导演",
                    "payload": {"motion_intensity": "low"},
                    "locked_fields": ["motion_intensity"],
                },
            )
        )
        preset = next(m for m in messages if m["id"] == "dp")["result"]
        await runtime.handle(
            Request(
                "dpa",
                "director.approve",
                {"revision_id": preset["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "dr",
                "director.resolve",
                {"preset_id": preset["id"]},
            )
        )
        await runtime.handle(Request("ov", "director.overview", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["vr"]["result"]["effective"]["style_name"] == "donghua"
    assert by_id["vr"]["result"]["effective"]["mood"] == "rain"
    assert by_id["dr"]["result"]["effective"]["motion_intensity"] == "low"
    assert by_id["ov"]["result"]["visual_bibles"]


def test_director_errors(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "DirErr")
    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    director = DirectorService(db)
    branch = story.primary_branch_id()

    with pytest.raises(ValueError, match="style_name"):
        director.create_visual_bible(
            branch_id=branch, name="x", style_name=" "
        )
    with pytest.raises(ValueError, match="scope_ref"):
        director.create_visual_bible(
            branch_id=branch,
            name="x",
            style_name="s",
            scope_level="episode",
        )
    with pytest.raises(ValueError, match="not found"):
        director.get_visual_bible("missing")
    with pytest.raises(ValueError, match="payload"):
        director.create_director_preset(
            branch_id=branch, name="p", payload={}
        )
    service.close()
