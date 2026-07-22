from __future__ import annotations

import json

import pytest

from workflow_sidecar.protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    Request,
    decode_request,
    error_response,
    event,
    success_response,
)


def test_decode_request_accepts_versioned_envelope() -> None:
    request = decode_request(
        json.dumps(
            {
                "v": 1,
                "type": "request",
                "id": "req_01",
                "method": "system.ping",
                "params": {"echo": "hello"},
            }
        ).encode()
    )

    assert request == Request(
        id="req_01", method="system.ping", params={"echo": "hello"}
    )


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"not-json", "INVALID_JSON"),
        (b"[]", "INVALID_ENVELOPE"),
        (b'{"v":2,"type":"request","id":"x","method":"system.ping"}', "UNSUPPORTED_VERSION"),
        (b'{"v":1,"type":"event","id":"x","method":"system.ping"}', "INVALID_ENVELOPE"),
        (b'{"v":1,"type":"request","id":"","method":"system.ping"}', "INVALID_REQUEST"),
        (b'{"v":1,"type":"request","id":"x","method":"","params":{}}', "INVALID_REQUEST"),
        (b'{"v":1,"type":"request","id":"x","method":"system.ping","params":[]}', "INVALID_REQUEST"),
    ],
)
def test_decode_request_rejects_invalid_messages(payload: bytes, code: str) -> None:
    with pytest.raises(ProtocolError) as caught:
        decode_request(payload)

    assert caught.value.code == code


def test_decode_request_enforces_message_limit() -> None:
    with pytest.raises(ProtocolError) as caught:
        decode_request(b"x" * (MAX_MESSAGE_BYTES + 1))

    assert caught.value.code == "MESSAGE_TOO_LARGE"


def test_envelope_builders_are_stable() -> None:
    assert success_response("r1", {"ok": True}) == {
        "v": 1,
        "type": "response",
        "id": "r1",
        "result": {"ok": True},
    }
    assert error_response("r2", "BAD_INPUT", "Invalid input", "diag_1") == {
        "v": 1,
        "type": "response",
        "id": "r2",
        "error": {
            "code": "BAD_INPUT",
            "message": "Invalid input",
            "diagnostic_id": "diag_1",
        },
    }
    assert event("request.progress", {"request_id": "r3"}) == {
        "v": 1,
        "type": "event",
        "event": "request.progress",
        "data": {"request_id": "r3"},
    }
