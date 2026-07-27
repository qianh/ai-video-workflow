from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.characters import CharacterService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_character_relationship_voice_flow(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Chars")
    assert project.schema_version >= 10

    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    chars = CharacterService(db)
    branch_id = story.primary_branch_id()

    incomplete = chars.create_character(
        branch_id=branch_id,
        name="阿宁",
        role="protagonist",
        personality=["冷静"],
    )
    rev_id = incomplete["current_revision"]["id"]
    rejected = chars.validate_character_revision(rev_id)
    assert rejected["valid"] is False
    assert any("appearance" in e for e in rejected["validation_errors"])

    chars.update_character_revision(
        rev_id,
        appearance_rules="短发，雨衣，冷色调",
        age_feel="二十出头",
        body_type="纤瘦",
        goals="查清失踪真相",
        immutable_traits=["左眉疤"],
    )
    approved = chars.approve_character_revision(rev_id)
    assert approved["current_revision_id"] == rev_id
    assert approved["current_revision"]["status"] == "approved"
    assert approved["current_revision"]["contains_media_prompts"] is False

    other = chars.create_character(
        branch_id=branch_id,
        name="陈叔",
        role="supporting",
        appearance_rules="中年，摊主围裙",
        personality=["热心", "碎嘴"],
    )
    other_rev = other["current_revision"]["id"]
    chars.approve_character_revision(other_rev)

    with pytest.raises(ValueError, match="must differ"):
        chars.create_relationship(
            branch_id=branch_id,
            source_character_id=approved["id"],
            target_character_id=approved["id"],
            relationship_type="self",
            description="x",
        )

    rel = chars.create_relationship(
        branch_id=branch_id,
        source_character_id=approved["id"],
        target_character_id=other["id"],
        relationship_type="acquaintance",
        description="夜市摊主认识阿宁",
        story_time_from="E01",
    )
    rel_rev = rel["current_revision"]["id"]
    rel_ok = chars.approve_relationship_revision(rel_rev)
    assert rel_ok["current_revision"]["status"] == "approved"

    voice = chars.create_voice_profile(
        character_id=approved["id"],
        label="阿宁默认",
        engine_adapter_id="local-tts",
        speed=1.05,
        emotion_range=["平静", "警惕"],
        pronunciation_rules={"U盘": "优盘"},
    )
    voice_ok = chars.approve_voice_revision(voice["current_revision"]["id"])
    assert voice_ok["current_revision"]["status"] == "approved"
    assert voice_ok["current_revision"]["contains_media_prompts"] is False

    narr = chars.create_voice_profile(
        character_id=None,
        label="旁白",
        emotion_range=["中性"],
    )
    chars.approve_voice_revision(narr["current_revision"]["id"])

    overview = chars.continuity_overview(branch_id)
    assert len(overview["characters"]) == 2
    assert len(overview["relationships"]) == 1
    assert len(overview["voice_profiles"]) >= 2
    service.close()


def test_character_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "CharRPC"},
            )
        )
        await runtime.handle(
            Request(
                "a",
                "character.create",
                {
                    "name": "阿宁",
                    "role": "protagonist",
                    "appearance_rules": "短发雨衣",
                    "personality": ["冷静"],
                    "goals": "找真相",
                },
            )
        )
        await runtime.handle(
            Request(
                "b",
                "character.create",
                {
                    "name": "陈叔",
                    "role": "supporting",
                    "appearance_rules": "围裙",
                    "personality": ["热心"],
                },
            )
        )
        a = next(m for m in messages if m["id"] == "a")["result"]
        b = next(m for m in messages if m["id"] == "b")["result"]
        await runtime.handle(
            Request(
                "va",
                "character.validate",
                {"revision_id": a["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "aa",
                "character.approve",
                {"revision_id": a["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "ab",
                "character.approve",
                {"revision_id": b["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "rel",
                "relationship.create",
                {
                    "source_character_id": a["id"],
                    "target_character_id": b["id"],
                    "relationship_type": "acquaintance",
                    "description": "摊主与客人",
                },
            )
        )
        rel = next(m for m in messages if m["id"] == "rel")["result"]
        await runtime.handle(
            Request(
                "rel_ok",
                "relationship.approve",
                {"revision_id": rel["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "voice",
                "voice.create",
                {
                    "character_id": a["id"],
                    "label": "阿宁",
                    "emotion_range": ["平静"],
                },
            )
        )
        voice = next(m for m in messages if m["id"] == "voice")["result"]
        await runtime.handle(
            Request(
                "voice_ok",
                "voice.approve",
                {"revision_id": voice["current_revision"]["id"]},
            )
        )
        await runtime.handle(Request("list", "character.list", {}))
        await runtime.handle(Request("rels", "relationship.list", {}))
        await runtime.handle(Request("voices", "voice.list", {}))
        await runtime.handle(Request("ov", "character.overview", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["va"]["result"]["valid"] is True
    assert by_id["aa"]["result"]["current_revision"]["status"] == "approved"
    assert by_id["rel_ok"]["result"]["current_revision"]["status"] == "approved"
    assert by_id["voice_ok"]["result"]["current_revision"]["status"] == "approved"
    assert len(by_id["list"]["result"]["characters"]) == 2
    assert by_id["ov"]["result"]["characters"]


def test_character_error_paths(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "CharErr")
    db = service.require_project_db()
    story = StoryService(db, Path(project.root_path))
    chars = CharacterService(db)
    branch = story.primary_branch_id()

    with pytest.raises(ValueError, match="name"):
        chars.create_character(branch_id=branch, name=" ")
    with pytest.raises(ValueError, match="role"):
        chars.create_character(branch_id=branch, name="X", role="hero")
    with pytest.raises(ValueError, match="personality"):
        chars.create_character(
            branch_id=branch, name="X", personality="calm"  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="not found"):
        chars.get_character("missing")
    with pytest.raises(ValueError, match="limit"):
        chars.list_characters(limit=0)

    c = chars.create_character(
        branch_id=branch,
        name="A",
        role="extra",
        appearance_rules="rules",
        personality=["quiet"],
    )
    rev = c["current_revision"]["id"]
    chars.approve_character_revision(rev)
    with pytest.raises(ValueError, match="not editable"):
        chars.update_character_revision(rev, name="B")
    with pytest.raises(ValueError, match="cannot validate"):
        chars.validate_character_revision(rev)

    with pytest.raises(ValueError, match="speed"):
        chars.create_voice_profile(speed=0)
    with pytest.raises(ValueError, match="pronunciation_rules"):
        chars.create_voice_profile(
            pronunciation_rules=[]  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="voice profile not found"):
        chars.get_voice_profile("missing")
    with pytest.raises(ValueError, match="relationship not found"):
        chars.get_relationship("missing")
    service.close()
