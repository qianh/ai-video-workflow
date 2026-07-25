from __future__ import annotations

import asyncio
from pathlib import Path

from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_project_create_open_list_close_rpc(tmp_path: Path) -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(
            messages.append,
            global_db_path=tmp_path / "global.db",
        )
        parent = tmp_path / "projects"
        parent.mkdir()
        await runtime.handle(
            Request(
                "c1",
                "project.create",
                {"parent_dir": str(parent), "name": "RPC Demo"},
            )
        )
        await runtime.handle(Request("l1", "project.list_recent", {"limit": 5}))
        await runtime.handle(Request("cur1", "project.current", {}))
        await runtime.handle(Request("cl1", "project.close", {}))
        await runtime.handle(Request("cur2", "project.current", {}))
        await runtime.shutdown()
        return messages

    messages = run(scenario())
    create = next(m for m in messages if m["id"] == "c1")
    assert create["type"] == "response"
    assert create["result"]["name"] == "RPC Demo"
    assert Path(create["result"]["root_path"]).joinpath("project.db").is_file()

    listed = next(m for m in messages if m["id"] == "l1")
    assert len(listed["result"]["projects"]) == 1

    current = next(m for m in messages if m["id"] == "cur1")
    assert current["result"]["project"]["id"] == create["result"]["id"]

    closed_current = next(m for m in messages if m["id"] == "cur2")
    assert closed_current["result"]["project"] is None
