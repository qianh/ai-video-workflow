from __future__ import annotations

import asyncio

from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_ping_echoes_payload_and_protocol_version() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(messages.append, enable_test_methods=True)
        await runtime.handle(
            Request("ping_1", "system.ping", {"echo": "hello"})
        )
        return messages

    assert run(scenario()) == [
        {
            "v": 1,
            "type": "response",
            "id": "ping_1",
            "result": {"status": "ok", "protocol_version": 1, "echo": "hello"},
        }
    ]


def test_unknown_method_returns_stable_error() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(messages.append, enable_test_methods=True)
        await runtime.handle(Request("bad_1", "missing.method", {}))
        return messages

    messages = run(scenario())
    assert messages[0]["id"] == "bad_1"
    assert messages[0]["error"] == {
        "code": "METHOD_NOT_FOUND",
        "message": "Unknown method: missing.method",
    }


def test_count_emits_progress_then_response() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(messages.append, enable_test_methods=True)
        await runtime.handle(
            Request("count_1", "diagnostics.count", {"steps": 3, "delay_ms": 0})
        )
        return messages

    messages = run(scenario())
    assert [message.get("type") for message in messages] == [
        "event",
        "event",
        "event",
        "response",
    ]
    assert messages[0]["data"] == {"request_id": "count_1", "current": 1, "total": 3}
    assert messages[-1]["result"] == {"completed_steps": 3}


def test_cancel_stops_inflight_request() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        first_progress = asyncio.Event()

        def emit(message: dict[str, object]) -> None:
            messages.append(message)
            if message.get("type") == "event":
                first_progress.set()

        runtime = SidecarRuntime(emit, enable_test_methods=True)
        task = asyncio.create_task(
            runtime.handle(
                Request(
                    "count_cancel",
                    "diagnostics.count",
                    {"steps": 100, "delay_ms": 10},
                )
            )
        )
        await asyncio.wait_for(first_progress.wait(), timeout=1)
        await runtime.handle(
            Request("cancel_1", "request.cancel", {"request_id": "count_cancel"})
        )
        await task
        return messages

    messages = run(scenario())
    by_id = {message.get("id"): message for message in messages if "id" in message}
    assert by_id["cancel_1"]["result"] == {"request_id": "count_cancel", "cancelled": True}
    assert by_id["count_cancel"]["error"]["code"] == "CANCELLED"


def test_duplicate_request_id_is_rejected_while_running() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        first_progress = asyncio.Event()

        def emit(message: dict[str, object]) -> None:
            messages.append(message)
            if message.get("type") == "event":
                first_progress.set()

        runtime = SidecarRuntime(emit, enable_test_methods=True)
        first = asyncio.create_task(
            runtime.handle(
                Request("same", "diagnostics.count", {"steps": 10, "delay_ms": 10})
            )
        )
        await asyncio.wait_for(first_progress.wait(), timeout=1)
        await runtime.handle(Request("same", "system.ping", {}))
        await runtime.handle(Request("cancel", "request.cancel", {"request_id": "same"}))
        await first
        return messages

    responses = [
        message
        for message in run(scenario())
        if message.get("type") == "response" and message.get("id") == "same"
    ]
    assert any(response.get("error", {}).get("code") == "DUPLICATE_REQUEST_ID" for response in responses)


def test_test_methods_are_disabled_by_default() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(messages.append, enable_test_methods=False)
        await runtime.handle(Request("count", "diagnostics.count", {"steps": 1}))
        return messages

    assert run(scenario())[0]["error"]["code"] == "METHOD_NOT_FOUND"


def test_invalid_count_params_return_validation_error() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(messages.append, enable_test_methods=True)
        await runtime.handle(
            Request("bad_count", "diagnostics.count", {"steps": True})
        )
        return messages

    assert run(scenario())[0]["error"] == {
        "code": "INVALID_PARAMS",
        "message": "steps must be an integer",
    }


def test_cancel_validates_target_and_reports_missing_request() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(messages.append, enable_test_methods=True)
        await runtime.handle(Request("missing_param", "request.cancel", {}))
        await runtime.handle(
            Request("missing_target", "request.cancel", {"request_id": "absent"})
        )
        return messages

    messages = run(scenario())
    assert messages[0]["error"]["code"] == "INVALID_PARAMS"
    assert messages[1]["result"] == {"request_id": "absent", "cancelled": False}


def test_unexpected_handler_error_is_redacted() -> None:
    async def scenario() -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(messages.append, enable_test_methods=True)

        async def explode(_request: Request) -> None:
            raise RuntimeError("secret detail")

        runtime._execute = explode  # type: ignore[method-assign]
        await runtime.handle(Request("explode", "system.ping", {}))
        return messages

    error = run(scenario())[0]["error"]
    assert error == {
        "code": "INTERNAL_ERROR",
        "message": "The sidecar could not complete the request",
    }
    assert "secret" not in str(error)
