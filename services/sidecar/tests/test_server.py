from __future__ import annotations

import asyncio
from io import BytesIO

from workflow_sidecar import server


class FakeStdin:
    def __init__(self, payload: bytes) -> None:
        self.buffer = BytesIO(payload)


def test_emit_writes_one_compact_json_line(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    writes: list[str] = []

    class FakeStdout:
        def write(self, value: str) -> None:
            writes.append(value)

        def flush(self) -> None:
            writes.append("<flush>")

    monkeypatch.setattr(server.sys, "stdout", FakeStdout())

    server.emit({"v": 1, "type": "event", "data": {"text": "中文"}})

    assert writes == [
        '{"v":1,"type":"event","data":{"text":"中文"}}',
        "\n",
        "<flush>",
    ]


def test_serve_handles_valid_invalid_and_inflight_messages(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    payload = b"\n".join(
        [
            b"not-json",
            b'{"v":1,"type":"request","id":"ping","method":"system.ping","params":{}}',
            b'{"v":1,"type":"request","id":"count","method":"diagnostics.count","params":{"steps":100,"delay_ms":10}}',
            b"",
        ]
    )
    messages: list[dict[str, object]] = []
    original_to_thread = asyncio.to_thread

    async def yielding_to_thread(function, *args):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return await original_to_thread(function, *args)

    monkeypatch.setenv("WORKFLOW_SIDECAR_ENABLE_TEST_METHODS", "1")
    monkeypatch.setattr(server.sys, "stdin", FakeStdin(payload))
    monkeypatch.setattr(server, "emit", messages.append)
    monkeypatch.setattr(server.asyncio, "to_thread", yielding_to_thread)

    asyncio.run(server.serve())

    assert messages[0]["error"]["code"] == "INVALID_JSON"
    assert any(message.get("id") == "ping" and "result" in message for message in messages)
    assert any(
        message.get("id") == "count"
        and message.get("error", {}).get("code") == "CANCELLED"
        for message in messages
    )
