from __future__ import annotations

import asyncio
import os
from pathlib import Path

from workflow_sidecar.adapters.components import (
    install_guide,
    probe_components,
    register_component,
)
from workflow_sidecar.adapters.rate_limit import (
    acquire_grok_slot,
    rate_limit_status,
    reset_for_tests,
)
from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.assets import AssetService
from workflow_sidecar.persistence.postproduction import PostProductionService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_asset_preview_data_url(tmp_path: Path) -> None:
    ws = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = ws.create_project(parent, "Preview")
    root = Path(project.root_path)
    assets = AssetService(ws.require_project_db(), root)
    asset = assets.create_asset(
        title="still",
        asset_type="image",
        role="preview",
        bytes_data=b"\xff\xd8\xffmockjpeg",
        mime_type="image/jpeg",
        license_status="confirmed_by_user",
    )
    preview = assets.preview_asset(asset["id"])
    assert preview["mode"] == "data_url"
    assert preview["data_url"].startswith("data:image/jpeg;base64,")
    assert preview["absolute_path"]
    ws.close()


def test_timeline_clip_edit_and_reorder(tmp_path: Path) -> None:
    ws = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    project = ws.create_project(parent, "TL")
    db = ws.require_project_db()
    root = Path(project.root_path)
    branch = StoryService(db, root).primary_branch_id()
    post = PostProductionService(db, root)
    # ensure an episode exists via story package bootstrap path is heavy;
    # create timeline with synthetic episode id is allowed by schema.
    tl = post.create_timeline(episode_id="ep-demo")
    rev = tl["current_revision"]
    assert rev is not None
    video = next(t for t in rev["tracks"] if t["track_type"] == "video")
    # insert two clips via assemble needs storyboard; write clips directly
    from workflow_sidecar.persistence.timeutil import utc_now
    import uuid
    import json

    now = utc_now()
    ids = []
    cursor = 0
    for _ in range(3):
        cid = str(uuid.uuid4())
        ids.append(cid)
        db.execute(
            """
            INSERT INTO timeline_clips(
                id, track_id, asset_file_id, shot_revision_id, start_ms, end_ms,
                source_in_ms, source_out_ms, params_json, created_at
            ) VALUES (?, ?, NULL, NULL, ?, ?, 0, ?, '{}', ?)
            """,
            (cid, video["id"], cursor, cursor + 1000, 1000, now),
        )
        cursor += 1000
    db.commit()
    post._recompute_duration(rev["id"])
    moved = post.move_clip(ids[0], direction="down")
    clips = next(t for t in moved["tracks"] if t["track_type"] == "video")["clips"]
    assert clips[0]["id"] == ids[1]
    assert clips[1]["id"] == ids[0]
    updated = post.update_clip(ids[1], end_ms=2500)
    assert updated["end_ms"] == 2500
    listed = post.list_timelines(limit=10)
    assert len(listed) >= 1
    ws.close()


def test_rate_limit_budget(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    reset_for_tests()
    monkeypatch.setenv("WORKFLOW_GROK_MAX_CALLS", "2")
    monkeypatch.setenv("WORKFLOW_GROK_MIN_INTERVAL_MS", "0")
    acquire_grok_slot(tool="image_gen")
    acquire_grok_slot(tool="image_gen")
    try:
        acquire_grok_slot(tool="image_gen")
        raised = False
    except RuntimeError:
        raised = True
    assert raised is True
    status = rate_limit_status()
    assert status["calls"] == 2
    assert status["blocked"] >= 1
    reset_for_tests()


def test_components_probe_and_register(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WORKFLOW_COMPONENTS_DIR", str(tmp_path / "comps"))
    info = probe_components()
    assert "cosyvoice3" in info["components"]
    guide = install_guide()
    assert guide["steps"]
    fake = tmp_path / "fake-cosy"
    fake.write_text("#!/bin/sh\necho cosy\n", encoding="utf-8")
    fake.chmod(0o755)
    reg = register_component("cosyvoice3", binary=str(fake), version="test")
    assert reg["components"]["cosyvoice3"]["status"] == "ready"


def test_media_workspace_rpc(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WORKFLOW_COMPONENTS_DIR", str(tmp_path / "comps"))

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
                {"parent_dir": str(parent), "name": "MediaWS"},
            )
        )
        await runtime.handle(Request("probe", "components.probe", {}))
        await runtime.handle(Request("guide", "components.guide", {}))
        await runtime.handle(Request("rate", "grok.rate_status", {}))
        from workflow_sidecar.persistence.assets import AssetService

        db = runtime._workspace.require_project_db()
        root = Path(runtime._workspace.current.root_path)  # type: ignore[union-attr]
        asset = AssetService(db, root).create_asset(
            title="p",
            asset_type="image",
            bytes_data=b"abc",
            mime_type="image/jpeg",
        )
        await runtime.handle(
            Request("prev", "asset.preview", {"asset_id": asset["id"]})
        )
        await runtime.handle(
            Request("tls", "timeline.create", {"episode_id": "e1"})
        )
        await runtime.handle(Request("tll", "timeline.list", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["probe"]["result"]["components"]["cosyvoice3"]["status"]
    assert by_id["prev"]["result"]["mode"] == "data_url"
    assert by_id["tll"]["result"]["timelines"]
