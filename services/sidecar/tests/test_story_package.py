from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.persistence.story_package import StoryPackageService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_world_rules_timeline_package_flow(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Package")
    assert project.schema_version >= 11

    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    pkgs = StoryPackageService(db)
    branch_id = story.primary_branch_id()

    hard = pkgs.add_world_rule(
        branch_id=branch_id,
        category="continuity",
        rule_text="forbid:时间旅行",
        force_level="hard",
    )
    soft = pkgs.add_world_rule(
        branch_id=branch_id,
        category="tone",
        rule_text="保持冷色夜市氛围",
        force_level="soft",
    )
    assert hard["force_level"] == "hard"
    assert soft["status"] == "active"
    assert len(pkgs.list_world_rules(branch_id)) == 2

    conflicts = pkgs.check_hard_rule_conflicts(branch_id, ["剧情含时间旅行桥段"])
    assert conflicts and conflicts[0]["force_level"] == "hard"
    assert pkgs.check_hard_rule_conflicts(branch_id, ["雨夜追逐"]) == []

    beat1 = pkgs.add_timeline_beat(
        branch_id=branch_id,
        beat_no=1,
        title="发现",
        summary="雨夜捡到发光 U 盘",
        arc_tag="setup",
        episode_nos=[1],
    )
    beat2 = pkgs.add_timeline_beat(
        branch_id=branch_id,
        beat_no=2,
        title="追索",
        summary="追查失踪消息来源",
        arc_tag="rising",
        episode_nos=[2, 3],
    )
    assert len(pkgs.list_timeline(branch_id)) == 2

    episodes = pkgs.ensure_episodes(branch_id=branch_id, count=3)
    assert len(episodes) == 3
    assert episodes[0]["title"] == "第1集"
    # idempotent
    again = pkgs.ensure_episodes(branch_id=branch_id, count=3)
    assert [item["id"] for item in again] == [item["id"] for item in episodes]

    draft_pkg = pkgs.create_package_revision(
        branch_id=branch_id,
        name="空草稿包",
        positioning={"theme": "都市悬疑"},
        world_rule_ids=[hard["id"], soft["id"]],
    )
    assert draft_pkg["status"] == "draft"
    assert draft_pkg["contains_media_prompts"] is False
    with pytest.raises(ValueError, match="draft package"):
        pkgs.approve_package_revision(draft_pkg["id"])

    with pytest.raises(ValueError, match="hard world rule conflict"):
        pkgs.create_package_revision(
            branch_id=branch_id,
            name="冲突包",
            positioning={"theme": "科幻"},
            world_rule_ids=[hard["id"]],
            timeline_beat_ids=[beat1["id"]],
            episode_ids=[episodes[0]["id"]],
            claims_for_rules=["引入时间旅行设定"],
        )

    validated = pkgs.create_package_revision(
        branch_id=branch_id,
        name="试播季故事包",
        positioning={"theme": "都市悬疑", "audience": "短剧"},
        world_rule_ids=[hard["id"], soft["id"]],
        timeline_beat_ids=[beat1["id"], beat2["id"]],
        episode_ids=[episodes[0]["id"], episodes[1]["id"], episodes[2]["id"]],
        notes="M2-07 sample",
        claims_for_rules=["雨夜追逐"],
    )
    assert validated["status"] == "validated"
    assert validated["contains_media_prompts"] is False

    approved = pkgs.approve_package_revision(validated["id"])
    assert approved["status"] == "approved"

    overview = pkgs.season_overview(branch_id)
    assert len(overview["world_rules"]) == 2
    assert len(overview["timeline"]) == 2
    assert len(overview["episodes"]) == 3
    assert any(item["status"] == "approved" for item in overview["packages"])
    service.close()


def test_story_package_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "PkgRPC"},
            )
        )
        await runtime.handle(
            Request(
                "rule",
                "world.add_rule",
                {
                    "category": "continuity",
                    "rule_text": "forbid:平行宇宙",
                    "force_level": "hard",
                },
            )
        )
        await runtime.handle(
            Request(
                "beat",
                "season.add_beat",
                {
                    "beat_no": 1,
                    "title": "开端",
                    "summary": "夜市发现",
                    "episode_nos": [1],
                },
            )
        )
        await runtime.handle(
            Request("eps", "season.ensure_episodes", {"count": 2})
        )
        rule_id = next(m for m in messages if m["id"] == "rule")["result"]["id"]
        beat_id = next(m for m in messages if m["id"] == "beat")["result"]["id"]
        episode_ids = [
            item["id"]
            for item in next(m for m in messages if m["id"] == "eps")["result"][
                "episodes"
            ]
        ]
        await runtime.handle(
            Request(
                "pkg",
                "package.create",
                {
                    "name": "RPC 故事包",
                    "positioning": {"theme": "悬疑"},
                    "world_rule_ids": [rule_id],
                    "timeline_beat_ids": [beat_id],
                    "episode_ids": episode_ids,
                },
            )
        )
        rev_id = next(m for m in messages if m["id"] == "pkg")["result"]["id"]
        await runtime.handle(
            Request("approve", "package.approve", {"revision_id": rev_id})
        )
        await runtime.handle(Request("overview", "season.overview", {}))
        await runtime.handle(Request("list", "package.list", {"limit": 10}))
        await runtime.handle(
            Request(
                "check",
                "world.check_conflicts",
                {"claims": ["引入平行宇宙"]},
            )
        )
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["rule"]["result"]["force_level"] == "hard"
    assert by_id["eps"]["result"]["episodes"][0]["episode_no"] == 1
    assert by_id["pkg"]["result"]["status"] == "validated"
    assert by_id["pkg"]["result"]["contains_media_prompts"] is False
    assert by_id["approve"]["result"]["status"] == "approved"
    assert len(by_id["overview"]["result"]["episodes"]) == 2
    assert by_id["check"]["result"]["blocked"] is True
    assert by_id["list"]["result"]["revisions"]
