from __future__ import annotations

import asyncio
import json
from pathlib import Path

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.acceptance import AcceptanceService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def _project(tmp_path: Path, name: str = "M5"):
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir(exist_ok=True)
    project = service.create_project(parent, name)
    db = service.require_project_db()
    root = Path(project.root_path)
    branch = StoryService(db, root).primary_branch_id()
    return service, db, root, branch


def test_acceptance_pilot(tmp_path: Path) -> None:
    service, db, root, branch = _project(tmp_path, "Pilot")
    acc = AcceptanceService(db, root)
    pilot = acc.run_pilot(branch_id=branch, force_mock_render=True)
    assert pilot["passed"] is True
    assert pilot["export_count"] == 3
    assert pilot["shot_count"] >= 6
    failed = [c for c in pilot["checks"] if not c["pass"]]
    assert failed == []
    service.close()


def test_acceptance_series_and_scale(tmp_path: Path) -> None:
    service, db, root, branch = _project(tmp_path, "Series")
    acc = AcceptanceService(db, root)
    pilot = acc.run_pilot(branch_id=branch, force_mock_render=True)
    series = acc.run_series(
        branch_id=branch,
        episode_count=3,
        shot_count=6,
        force_mock_render=True,
        character_id=pilot.get("character_id"),
    )
    assert series["passed"] is True, series["checks"]
    assert series["episode_count"] >= 2
    assert series["rework"]["ok"] is True
    assert series["rework"]["stale_count"] >= 1

    scale = acc.run_scale(
        branch_id=branch,
        episode_count=4,
        shot_count=6,
        force_mock_render=True,
        character_id=pilot.get("character_id"),
        voice_auth_id=pilot.get("voice_auth_id"),
        music_item_id=pilot.get("music_item_id"),
    )
    assert scale["passed"] is True
    assert scale["metrics"]["episodes"] == 4
    assert all(m["export_count"] == 3 for m in scale["metrics"]["per_episode"])
    service.close()


def test_acceptance_run_all_writes_report(tmp_path: Path) -> None:
    service, db, root, branch = _project(tmp_path, "All")
    acc = AcceptanceService(db, root)
    report = acc.run_all(
        branch_id=branch,
        series_episodes=2,
        scale_episodes=3,
        shot_count=6,
        force_mock_render=True,
    )
    assert report["phase"] == "M5"
    assert report["grade"] in {"pass", "conditional_pass", "fail"}
    assert report["grade"] != "fail"
    assert report["pilot"]["passed"] is True
    assert report["series_5"]["passed"] is True
    assert report["series_20"]["passed"] is True
    json_path = root / report["report_json"]
    md_path = root / report["report_md"]
    assert json_path.is_file()
    assert md_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["grade"] == report["grade"]
    assert "human_review_checklist" in payload
    service.close()


def test_acceptance_rpc(tmp_path: Path) -> None:
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
                {"parent_dir": str(parent), "name": "M5RPC"},
            )
        )
        await runtime.handle(
            Request(
                "m5",
                "trial.accept_m5",
                {
                    "mode": "all",
                    "series_episodes": 2,
                    "scale_episodes": 2,
                    "shot_count": 6,
                    "force_mock_render": True,
                },
            )
        )
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    result = by_id["m5"]["result"]
    assert result["phase"] == "M5"
    assert result["grade"] in {"pass", "conditional_pass"}
    assert result["pilot"]["passed"] is True
