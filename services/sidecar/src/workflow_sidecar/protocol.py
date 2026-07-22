"""Versioned NDJSON protocol primitives shared by the sidecar runtime."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Request:
    """A validated host-to-sidecar request."""

    id: str
    method: str
    params: JsonObject


class ProtocolError(ValueError):
    """An error that is safe to expose through the IPC envelope."""

    def __init__(self, code: str, message: str, request_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


def decode_request(payload: bytes) -> Request:
    """Decode and validate one NDJSON request line."""

    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("MESSAGE_TOO_LARGE", "Message exceeds the 1 MiB limit")

    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError("INVALID_JSON", "Message is not valid UTF-8 JSON") from exc

    if not isinstance(value, dict):
        raise ProtocolError("INVALID_ENVELOPE", "Message envelope must be an object")

    request_id = value.get("id") if isinstance(value.get("id"), str) else None
    if value.get("v") != PROTOCOL_VERSION:
        raise ProtocolError(
            "UNSUPPORTED_VERSION",
            f"Only protocol version {PROTOCOL_VERSION} is supported",
            request_id,
        )
    if value.get("type") != "request":
        raise ProtocolError(
            "INVALID_ENVELOPE", "Host messages must have type=request", request_id
        )

    method = value.get("method")
    params = value.get("params", {})
    if not request_id or not isinstance(method, str) or not method:
        raise ProtocolError(
            "INVALID_REQUEST", "Request id and method must be non-empty strings", request_id
        )
    if not isinstance(params, dict):
        raise ProtocolError("INVALID_REQUEST", "Request params must be an object", request_id)

    return Request(id=request_id, method=method, params=params)


def success_response(request_id: str, result: JsonObject) -> JsonObject:
    return {
        "v": PROTOCOL_VERSION,
        "type": "response",
        "id": request_id,
        "result": result,
    }


def error_response(
    request_id: str | None,
    code: str,
    message: str,
    diagnostic_id: str | None = None,
) -> JsonObject:
    error: JsonObject = {"code": code, "message": message}
    if diagnostic_id is not None:
        error["diagnostic_id"] = diagnostic_id
    return {
        "v": PROTOCOL_VERSION,
        "type": "response",
        "id": request_id,
        "error": error,
    }


def event(name: str, data: JsonObject) -> JsonObject:
    return {"v": PROTOCOL_VERSION, "type": "event", "event": name, "data": data}
