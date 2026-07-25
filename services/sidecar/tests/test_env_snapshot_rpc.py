from __future__ import annotations

import asyncio
from pathlib import Path

from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_env_and_snapshot_rpc(tmp_path: Path) -> None:
    async def scenario() -> dict[str, dict[str, object]]:
        messages: list[dict[str, object]] = []
        global_db = tmp_path / "global.db"
        runtime = SidecarRuntime(messages.append, global_db_path=global_db)
        parent = tmp_path / "projects"
        parent.mkdir()
        (tmp_path / "env").write_text("PUBLIC=global\nAPI_KEY=gsecret\n", encoding="utf-8")
        await runtime.handle(
            Request(
                "p",
                "project.create",
                {"parent_dir": str(parent), "name": "EnvSnap"},
            )
        )
        root = Path(next(m for m in messages if m["id"] == "p")["result"]["root_path"])
        (root / ".env.local").write_text(
            "API_KEY=psecret\nNOTE=from-project\n", encoding="utf-8"
        )

        await runtime.handle(
            Request(
                "sum",
                "env.summary",
                {"keys": ["API_KEY", "NOTE", "PUBLIC", "MISSING"]},
            )
        )
        await runtime.handle(
            Request(
                "res",
                "env.resolve",
                {"allow_keys": ["NOTE", "API_KEY"]},
            )
        )
        await runtime.handle(
            Request("snap", "snapshot.create", {"reason": "manual-test"})
        )
        await runtime.handle(Request("list", "snapshot.list", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    bindings = {
        item["key"]: item for item in by_id["sum"]["result"]["bindings"]
    }
    assert bindings["API_KEY"]["source"] == "project"
    assert bindings["API_KEY"]["is_secret"] is True
    assert "value" not in bindings["API_KEY"]
    assert by_id["res"]["result"]["values"]["API_KEY"] == "psecret"
    assert by_id["res"]["result"]["values"]["NOTE"] == "from-project"
    assert by_id["snap"]["result"]["reason"] == "manual-test"
    assert len(by_id["list"]["result"]["snapshots"]) >= 1
