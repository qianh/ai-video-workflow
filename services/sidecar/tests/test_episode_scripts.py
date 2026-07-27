from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.episode_scripts import EpisodeScriptService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.persistence.story_package import StoryPackageService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_script_scene_dialogue_hook_approve_flow(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Scripts")
    assert project.schema_version >= 14

    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    pkgs = StoryPackageService(db)
    scripts = EpisodeScriptService(db)
    branch_id = story.primary_branch_id()

    episodes = pkgs.ensure_episodes(branch_id=branch_id, count=2)
    ep = episodes[0]
    assert ep["status"] == "planned"

    incomplete = scripts.create_script(
        episode_id=ep["id"],
        title="夜市开端",
        goal="建立悬念",
    )
    assert incomplete["status"] == "draft"
    assert incomplete["contains_media_prompts"] is False
    assert scripts.get_episode(ep["id"])["status"] == "scripting"

    rejected = scripts.validate_script(incomplete["id"])
    assert rejected["valid"] is False
    assert "opening_hook" in rejected["validation_errors"][0] or any(
        "opening_hook" in err for err in rejected["validation_errors"]
    )

    scripts.update_script(
        incomplete["id"],
        main_conflict="U 盘与失踪消息",
        opening_hook="雨夜发光的 U 盘",
        ending_hook="陌生号码打来电话",
        twist="U 盘是她失踪的自己寄回的",
        estimated_duration_ms=90_000,
    )
    scene = scripts.add_scene(
        script_revision_id=incomplete["id"],
        purpose="发现道具",
        action_text="女孩在摊位旁捡起发光 U 盘",
        time_of_day="night",
        location_ref="夜市东口",
        estimated_duration_ms=45_000,
    )
    scripts.add_scene(
        script_revision_id=incomplete["id"],
        purpose="追查线索",
        action_text="她插入电脑发现加密视频",
        time_of_day="night",
        location_ref="出租屋",
    )
    line = scripts.add_dialogue(
        scene_revision_id=scene["id"],
        speaker_name="阿宁",
        text="这光……不像普通 U 盘。",
        line_type="dialogue",
        emotion="警惕",
        action_intent="凑近查看",
    )
    assert line["revision_no"] == 1
    revised = scripts.revise_dialogue(
        line["line_id"], text="这光……像在叫我。"
    )
    assert revised["revision_no"] == 2
    assert revised["text"] == "这光……像在叫我。"

    scripts.add_dialogue(
        scene_revision_id=scene["id"],
        text="雨声盖过她的呼吸。",
        line_type="narration",
    )
    scripts.add_hook(
        script_revision_id=incomplete["id"],
        hook_type="mid",
        text="视频最后一帧是她自己的脸",
        position_scene_no=2,
    )
    scripts.add_hook(
        script_revision_id=incomplete["id"],
        hook_type="cliffhanger",
        text="来电显示：未知号码",
    )

    validated = scripts.validate_script(incomplete["id"])
    assert validated["valid"] is True
    assert validated["status"] == "validated"
    assert validated["content_hash"]

    tree = scripts.get_script_tree(incomplete["id"])
    assert len(tree["scenes"]) == 2
    assert len(tree["dialogue"]) == 2
    assert len(tree["hooks"]) == 2
    # list_dialogue returns latest revision only
    assert any(item["revision_no"] == 2 for item in tree["dialogue"])

    approved = scripts.approve_script(incomplete["id"])
    assert approved["status"] == "approved"
    assert approved["episode"]["current_script_revision_id"] == incomplete["id"]
    assert approved["episode"]["status"] == "script_review"
    assert approved["episode"]["title"] == "夜市开端"

    with pytest.raises(ValueError, match="not editable"):
        scripts.add_scene(
            script_revision_id=incomplete["id"],
            purpose="x",
            action_text="y",
        )

    service.close()


def test_script_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "ScriptRPC"},
            )
        )
        await runtime.handle(
            Request("eps", "season.ensure_episodes", {"count": 1})
        )
        episode_id = next(m for m in messages if m["id"] == "eps")["result"][
            "episodes"
        ][0]["id"]
        await runtime.handle(
            Request(
                "create",
                "script.create",
                {
                    "episode_id": episode_id,
                    "title": "E01",
                    "goal": "开场",
                    "main_conflict": "失踪",
                    "opening_hook": "发光 U 盘",
                    "ending_hook": "未知来电",
                },
            )
        )
        script_id = next(m for m in messages if m["id"] == "create")["result"]["id"]
        await runtime.handle(
            Request(
                "scene",
                "script.add_scene",
                {
                    "script_id": script_id,
                    "purpose": "发现",
                    "action_text": "捡到 U 盘",
                    "time_of_day": "night",
                },
            )
        )
        scene_id = next(m for m in messages if m["id"] == "scene")["result"]["id"]
        await runtime.handle(
            Request(
                "line",
                "script.add_dialogue",
                {
                    "scene_id": scene_id,
                    "speaker_name": "阿宁",
                    "text": "谁的？",
                    "line_type": "dialogue",
                },
            )
        )
        await runtime.handle(
            Request(
                "hook",
                "script.add_hook",
                {
                    "script_id": script_id,
                    "hook_type": "mid",
                    "text": "加密文件",
                },
            )
        )
        await runtime.handle(
            Request(
                "upd_scene",
                "script.update_scene",
                {
                    "scene_id": scene_id,
                    "purpose": "发现升级",
                    "action_text": "仔细查看 U 盘",
                },
            )
        )
        line_id = next(m for m in messages if m["id"] == "line")["result"][
            "line_id"
        ]
        await runtime.handle(
            Request(
                "rev_line",
                "script.revise_dialogue",
                {"line_id": line_id, "text": "谁留下的？"},
            )
        )
        await runtime.handle(
            Request("list_sc", "script.list_scenes", {"script_id": script_id})
        )
        await runtime.handle(
            Request("list_dlg", "script.list_dialogue", {"script_id": script_id})
        )
        await runtime.handle(
            Request("list_hk", "script.list_hooks", {"script_id": script_id})
        )
        await runtime.handle(
            Request("list_scpt", "script.list", {"episode_id": episode_id})
        )
        await runtime.handle(
            Request("get_ep", "episode.get", {"episode_id": episode_id})
        )
        await runtime.handle(
            Request(
                "title",
                "episode.update_title",
                {"episode_id": episode_id, "title": "E01 改名"},
            )
        )
        await runtime.handle(
            Request(
                "upd",
                "script.update",
                {"script_id": script_id, "notes": "rpc-note"},
            )
        )
        await runtime.handle(
            Request("get", "script.get", {"script_id": script_id})
        )
        await runtime.handle(
            Request("val", "script.validate", {"script_id": script_id})
        )
        await runtime.handle(
            Request("ok", "script.approve", {"script_id": script_id})
        )
        await runtime.handle(
            Request("tree", "script.tree", {"script_id": script_id})
        )
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["create"]["result"]["status"] == "draft"
    assert by_id["upd_scene"]["result"]["purpose"] == "发现升级"
    assert by_id["rev_line"]["result"]["revision_no"] == 2
    assert by_id["list_sc"]["result"]["scenes"]
    assert by_id["list_dlg"]["result"]["lines"]
    assert by_id["list_hk"]["result"]["hooks"]
    assert by_id["list_scpt"]["result"]["scripts"]
    assert by_id["title"]["result"]["title"] == "E01 改名"
    assert by_id["get"]["result"]["notes"] == "rpc-note"
    assert by_id["val"]["result"]["valid"] is True
    assert by_id["ok"]["result"]["status"] == "approved"
    assert by_id["tree"]["result"]["contains_media_prompts"] is False
    assert len(by_id["tree"]["result"]["scenes"]) == 1
    assert by_id["tree"]["result"]["dialogue"][0]["speaker_name"] == "阿宁"


def test_script_edit_paths_and_errors(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "ScriptEdit")
    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    pkgs = StoryPackageService(db)
    scripts = EpisodeScriptService(db)
    branch_id = story.primary_branch_id()
    ep = pkgs.ensure_episodes(branch_id=branch_id, count=1)[0]

    scripts.update_episode_title(ep["id"], "新标题")
    assert scripts.get_episode(ep["id"])["title"] == "新标题"
    with pytest.raises(ValueError, match="title"):
        scripts.update_episode_title(ep["id"], "  ")

    script = scripts.create_script(episode_id=ep["id"], title="草稿")
    scripts.update_script(
        script["id"],
        goal="g",
        main_conflict="c",
        opening_hook="o",
        ending_hook="e",
        twist="t",
        notes="n",
        estimated_duration_ms=1000,
    )
    scripts.update_script(script["id"], clear_twist=True)
    assert scripts.get_script(script["id"])["twist"] is None

    scene = scripts.add_scene(
        script_revision_id=script["id"],
        scene_no=1,
        purpose="p",
        action_text="a",
        time_of_day="day",
        location_ref="loc",
        estimated_duration_ms=500,
    )
    updated_scene = scripts.update_scene(
        scene["id"],
        purpose="新目的",
        action_text="新动作",
        time_of_day="dusk",
        clear_location=True,
        estimated_duration_ms=600,
    )
    assert updated_scene["purpose"] == "新目的"
    assert updated_scene["location_ref"] is None

    with pytest.raises(ValueError, match="time_of_day"):
        scripts.add_scene(
            script_revision_id=script["id"],
            purpose="x",
            action_text="y",
            time_of_day="noon",
        )
    with pytest.raises(ValueError, match="purpose and action_text"):
        scripts.add_scene(
            script_revision_id=script["id"], purpose=" ", action_text="y"
        )
    with pytest.raises(ValueError, match="dialogue lines require speaker_name"):
        scripts.add_dialogue(
            scene_revision_id=scene["id"],
            text="hi",
            line_type="dialogue",
        )
    line = scripts.add_dialogue(
        scene_revision_id=scene["id"],
        text="旁白",
        line_type="narration",
        sort_order=5,
    )
    with pytest.raises(ValueError, match="dialogue lines require speaker_name"):
        scripts.revise_dialogue(line["line_id"], line_type="dialogue", speaker_name="")

    scripts.add_hook(
        script_revision_id=script["id"],
        hook_type="opening",
        text="开场钩子同步",
    )
    # opening already set; ending empty path via hook
    empty_hooks = scripts.create_script(episode_id=ep["id"], title="hooks")
    scripts.add_hook(
        script_revision_id=empty_hooks["id"],
        hook_type="opening",
        text="O1",
    )
    scripts.add_hook(
        script_revision_id=empty_hooks["id"],
        hook_type="ending",
        text="E1",
    )
    synced = scripts.get_script(empty_hooks["id"])
    assert synced["opening_hook"] == "O1"
    assert synced["ending_hook"] == "E1"

    listed = scripts.list_scripts(episode_id=ep["id"], limit=10)
    assert len(listed) >= 2
    assert scripts.list_scripts(limit=5)
    by_scene = scripts.list_dialogue(scene_revision_id=scene["id"])
    assert by_scene
    with pytest.raises(ValueError, match="scene_revision_id or script_revision_id"):
        scripts.list_dialogue()

    with pytest.raises(ValueError, match="episode not found"):
        scripts.get_episode("missing")
    with pytest.raises(ValueError, match="script revision not found"):
        scripts.get_script("missing")
    with pytest.raises(ValueError, match="scene not found"):
        scripts.get_scene("missing")
    with pytest.raises(ValueError, match="hook not found"):
        scripts.get_hook("missing")
    with pytest.raises(ValueError, match="dialogue line not found"):
        scripts.get_latest_dialogue("missing")
    with pytest.raises(ValueError, match="dialogue revision not found"):
        scripts.get_dialogue_revision("missing")
    with pytest.raises(ValueError, match="hook_type"):
        scripts.add_hook(
            script_revision_id=script["id"], hook_type="bad", text="x"
        )
    with pytest.raises(ValueError, match="limit"):
        scripts.list_scripts(limit=0)

    # approve auto-validates draft
    ready = scripts.create_script(
        episode_id=ep["id"],
        title=None,
        goal="g",
        main_conflict="c",
        opening_hook="o",
        ending_hook="e",
    )
    s = scripts.add_scene(
        script_revision_id=ready["id"], purpose="p", action_text="a"
    )
    scripts.add_dialogue(
        scene_revision_id=s["id"], text="n", line_type="narration"
    )
    approved = scripts.approve_script(ready["id"])
    assert approved["status"] == "approved"
    assert approved["episode"]["current_script_revision_id"] == ready["id"]

    service.close()
