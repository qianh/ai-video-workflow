from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_log_and_diagnostics_and_fs_rpc(tmp_path: Path) -> None:
    async def scenario() -> dict[str, dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(
            messages.append, global_db_path=tmp_path / "global.db"
        )
        parent = tmp_path / "projects"
        parent.mkdir()
        await runtime.handle(
            Request(
                "p",
                "project.create",
                {"parent_dir": str(parent), "name": "Diag"},
            )
        )
        root = Path(next(m for m in messages if m["id"] == "p")["result"]["root_path"])
        asset = root / "assets" / "documents" / "note.txt"
        asset.write_text("hello-integrity", encoding="utf-8")

        await runtime.handle(
            Request(
                "log",
                "log.write",
                {
                    "level": "info",
                    "message": "user api_key=super-secret-value",
                    "fields": {"token": "abc", "ok": "yes"},
                },
            )
        )
        await runtime.handle(Request("tail", "log.tail", {"limit": 10}))
        await runtime.handle(Request("pack", "diagnostics.create_pack", {}))
        await runtime.handle(
            Request("resolve", "fs.resolve", {"relative": "assets/documents/note.txt"})
        )
        await runtime.handle(
            Request("hash", "fs.hash", {"relative": "assets/documents/note.txt"})
        )
        await runtime.handle(
            Request("escape", "fs.resolve", {"relative": "../outside.txt"})
        )
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    record = by_id["log"]["result"]["record"]
    assert "super-secret-value" not in record["message"]
    assert record["fields"]["token"] == "<redacted>"
    assert record["fields"]["ok"] == "yes"
    assert by_id["tail"]["result"]["records"]

    pack_path = Path(by_id["pack"]["result"]["path"])
    assert pack_path.is_file()
    with zipfile.ZipFile(pack_path) as archive:
        assert ".env.local" not in archive.namelist()
        names = archive.namelist()
        assert "meta.json" in names

    assert by_id["resolve"]["result"]["exists"] is True
    assert by_id["hash"]["result"]["sha256"]
    assert by_id["escape"]["error"]["code"] == "INVALID_PARAMS"
