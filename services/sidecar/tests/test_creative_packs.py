from __future__ import annotations

import asyncio
from pathlib import Path

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.creative_packs import CreativePackService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_register_compose_evaluate_lock(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Packs")
    packs = CreativePackService(service.require_project_db())

    visual = packs.register_pack(
        name="赛博夜景",
        pack_type="visual_style",
        rules={"palette": "neon", "ratio": "9:16"},
    )
    narrative = packs.register_pack(
        name="都市悬疑",
        pack_type="narrative_genre",
        rules={"hooks": "mystery", "pace": "fast"},
    )
    technique = packs.register_pack(
        name="Grok 图技",
        pack_type="model_technique",
        rules={"prompt_prefix": "manhua", "hard_ratio": "9:16"},
        resources={"required": ["lut_neon"], "available": ["lut_neon"]},
    )

    composed = packs.compose(
        name="夜市默认组合",
        visual_revision_id=visual["revision"]["id"],
        narrative_revision_id=narrative["revision"]["id"],
        technique_revision_ids=[technique["revision"]["id"]],
    )
    assert composed["status"] == "eligible"
    assert composed["resolved_rules"]["palette"] == "neon"
    assert composed["resolved_rules"]["hooks"] == "mystery"

    # Immutability: publishing new revision does not mutate old hash
    old_hash = visual["revision"]["content_hash"]
    updated = packs.publish_revision(
        visual["pack"]["id"],
        rules={"palette": "daylight", "ratio": "9:16"},
    )
    assert updated["version"] == 2
    assert packs.get_revision(visual["revision"]["id"])["content_hash"] == old_hash

    evaluation = packs.evaluate(composed["composition_revision_id"])
    assert evaluation["result"] == "pass"
    assert evaluation["status_after"] == "eligible"

    lock = packs.lock(composed["composition_revision_id"], purpose="production")
    assert lock["composition_content_hash"] == composed["content_hash"]
    assert packs.current_lock()["id"] == lock["id"]

    # Rejected composition cannot lock
    bad = packs.register_pack(
        name="冲突视觉",
        pack_type="visual_style",
        rules={"hard_ratio": "16:9"},
    )
    bad_narrative = packs.register_pack(
        name="冲突叙事",
        pack_type="narrative_genre",
        rules={"hard_ratio": "9:16"},
    )
    rejected = packs.compose(
        name="冲突组合",
        visual_revision_id=bad["revision"]["id"],
        narrative_revision_id=bad_narrative["revision"]["id"],
    )
    assert rejected["status"] == "rejected"
    try:
        packs.lock(rejected["composition_revision_id"])
        raise AssertionError("expected lock failure")
    except ValueError as exc:
        assert "eligible" in str(exc)

    service.close()


def test_pack_rpc_flow(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "PackRPC"},
            )
        )
        await runtime.handle(
            Request(
                "v",
                "pack.register",
                {
                    "name": "视觉",
                    "pack_type": "visual_style",
                    "rules": {"a": 1},
                },
            )
        )
        await runtime.handle(
            Request(
                "n",
                "pack.register",
                {
                    "name": "叙事",
                    "pack_type": "narrative_genre",
                    "rules": {"b": 2},
                },
            )
        )
        visual_rev = next(m for m in messages if m["id"] == "v")["result"]["revision"][
            "id"
        ]
        narrative_rev = next(m for m in messages if m["id"] == "n")["result"][
            "revision"
        ]["id"]
        await runtime.handle(
            Request(
                "compose",
                "pack.compose",
                {
                    "name": "默认",
                    "visual_revision_id": visual_rev,
                    "narrative_revision_id": narrative_rev,
                },
            )
        )
        comp_rev = next(m for m in messages if m["id"] == "compose")["result"][
            "composition_revision_id"
        ]
        await runtime.handle(
            Request(
                "eval",
                "pack.evaluate",
                {"composition_revision_id": comp_rev},
            )
        )
        await runtime.handle(
            Request(
                "lock",
                "pack.lock",
                {"composition_revision_id": comp_rev},
            )
        )
        await runtime.handle(Request("cur", "pack.current_lock", {}))
        await runtime.handle(Request("list", "pack.list", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["compose"]["result"]["status"] == "eligible"
    assert by_id["eval"]["result"]["result"] == "pass"
    assert by_id["lock"]["result"]["purpose"] == "production"
    assert by_id["cur"]["result"]["lock"]["id"] == by_id["lock"]["result"]["id"]
    assert len(by_id["list"]["result"]["packs"]) == 2
