from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.characters import CharacterService
from workflow_sidecar.persistence.identity_packs import IdentityPackService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def _approved_protagonist(chars: CharacterService, branch_id: str) -> dict:
    c = chars.create_character(
        branch_id=branch_id,
        name="阿宁",
        role="protagonist",
        appearance_rules="短发雨衣",
        personality=["冷静"],
        goals="找真相",
    )
    return chars.approve_character_revision(c["current_revision"]["id"])


def test_identity_pack_look_confirm_gate(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Looks")
    assert project.schema_version >= 16

    db = service.require_project_db()
    root = Path(project.root_path)
    story = StoryService(db, root)
    chars = CharacterService(db)
    packs = IdentityPackService(db, root)
    branch = story.primary_branch_id()
    hero = _approved_protagonist(chars, branch)

    gate0 = packs.production_gate(hero["id"])
    assert gate0["identity_pack_required"] is True
    assert gate0["ready_for_production"] is False

    pack = packs.create_pack(
        character_id=hero["id"],
        positive_prompt="cold-tone night market girl, short hair, raincoat",
        negative_prompt="blurry, extra limbs",
        height_cm=165,
        proportion_notes="head:body 1:7",
    )
    rev_id = pack["current_revision"]["id"]
    assert pack["contains_media_prompts"] is True

    rejected = packs.validate(rev_id)
    assert rejected["valid"] is False
    assert any("look candidate" in e for e in rejected["validation_errors"])

    generated = packs.generate_looks(rev_id, count=3)
    assert generated["count"] == 3
    cand = generated["candidates"][0]
    assert cand["source"] == "mock"
    asset = root / cand["asset_rel_path"]
    assert asset.is_file()

    selected = packs.select_look(cand["id"])
    assert selected["candidate"]["status"] == "selected"
    assert selected["revision"]["selected_candidate_id"] == cand["id"]

    validated = packs.validate(rev_id)
    assert validated["valid"] is True

    confirmed = packs.confirm(rev_id)
    assert confirmed["confirmed_revision_id"] == rev_id
    assert confirmed["confirmed_revision"]["status"] == "confirmed"
    assert confirmed["confirmed_revision"]["confirmed_at"]

    gate1 = packs.production_gate(hero["id"])
    assert gate1["ready_for_production"] is True
    assert gate1["confirmed_revision_id"] == rev_id

    with pytest.raises(ValueError, match="confirmed"):
        packs.generate_looks(rev_id, count=1)

    service.close()


def test_identity_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "IdRPC"},
            )
        )
        await runtime.handle(
            Request(
                "char",
                "character.create",
                {
                    "name": "阿宁",
                    "role": "protagonist",
                    "appearance_rules": "短发",
                    "personality": ["冷静"],
                },
            )
        )
        char = next(m for m in messages if m["id"] == "char")["result"]
        await runtime.handle(
            Request(
                "cap",
                "character.approve",
                {"revision_id": char["current_revision"]["id"]},
            )
        )
        await runtime.handle(
            Request(
                "pack",
                "identity.create",
                {
                    "character_id": char["id"],
                    "positive_prompt": "night market heroine look",
                    "negative_prompt": "blurry",
                    "height_cm": 165,
                },
            )
        )
        pack = next(m for m in messages if m["id"] == "pack")["result"]
        rev_id = pack["current_revision"]["id"]
        await runtime.handle(
            Request(
                "looks",
                "identity.generate_looks",
                {"revision_id": rev_id, "count": 2},
            )
        )
        looks = next(m for m in messages if m["id"] == "looks")["result"]
        cand_id = looks["candidates"][0]["id"]
        await runtime.handle(
            Request(
                "sel",
                "identity.select_look",
                {"candidate_id": cand_id},
            )
        )
        await runtime.handle(
            Request("val", "identity.validate", {"revision_id": rev_id})
        )
        await runtime.handle(
            Request("ok", "identity.confirm", {"revision_id": rev_id})
        )
        await runtime.handle(
            Request("gate", "identity.gate", {"character_id": char["id"]})
        )
        await runtime.handle(
            Request("list", "identity.list", {"character_id": char["id"]})
        )
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["looks"]["result"]["count"] == 2
    assert by_id["sel"]["result"]["candidate"]["status"] == "selected"
    assert by_id["val"]["result"]["valid"] is True
    assert by_id["ok"]["result"]["confirmed_revision"]["status"] == "confirmed"
    assert by_id["gate"]["result"]["ready_for_production"] is True
    assert by_id["list"]["result"]["packs"]


def test_identity_errors(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = service.create_project(parent, "IdErr")
    db = service.require_project_db()
    root = Path(project.root_path)
    story = StoryService(db, root)
    chars = CharacterService(db)
    packs = IdentityPackService(db, root)
    branch = story.primary_branch_id()
    hero = _approved_protagonist(chars, branch)

    with pytest.raises(ValueError, match="not found"):
        packs.get_pack("missing")
    with pytest.raises(ValueError, match="limit"):
        packs.list_packs(limit=0)
    with pytest.raises(ValueError, match="count"):
        pack = packs.create_pack(
            character_id=hero["id"], positive_prompt="p"
        )
        packs.generate_looks(pack["current_revision"]["id"], count=9)

    pack = packs.create_pack(character_id=hero["id"], positive_prompt="look")
    rev = pack["current_revision"]["id"]
    packs.generate_looks(rev, count=1)
    with pytest.raises(ValueError, match="selected_candidate"):
        packs.confirm(rev)

    # supporting character does not require pack
    side = chars.create_character(
        branch_id=branch,
        name="路人",
        role="extra",
        appearance_rules="crowd",
        personality=["quiet"],
    )
    chars.approve_character_revision(side["current_revision"]["id"])
    gate = packs.production_gate(side["id"])
    assert gate["identity_pack_required"] is False
    assert gate["ready_for_production"] is True
    service.close()
