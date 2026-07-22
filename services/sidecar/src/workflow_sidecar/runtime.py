"""Request dispatch and cancellation for the workflow sidecar."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from typing import Any

from .protocol import Request, error_response, event, success_response


Message = dict[str, Any]
Emitter = Callable[[Message], None]


class SidecarRuntime:
    """Runs requests without coupling protocol transport to business handlers."""

    def __init__(self, emit: Emitter, *, enable_test_methods: bool = False) -> None:
        self._emit = emit
        self._enable_test_methods = enable_test_methods
        self._active: dict[str, asyncio.Task[None]] = {}

    async def handle(self, request: Request) -> None:
        if request.method == "request.cancel":
            await self._cancel(request)
            return

        if request.id in self._active:
            self._emit(
                error_response(
                    request.id,
                    "DUPLICATE_REQUEST_ID",
                    f"Request is already running: {request.id}",
                )
            )
            return

        current = asyncio.current_task()
        if current is None:  # pragma: no cover - asyncio always supplies one here
            raise RuntimeError("SidecarRuntime.handle requires an asyncio task")
        self._active[request.id] = current
        try:
            await self._execute(request)
        except asyncio.CancelledError:
            self._emit(error_response(request.id, "CANCELLED", "Request was cancelled"))
        except ValueError as exc:
            self._emit(error_response(request.id, "INVALID_PARAMS", str(exc)))
        except Exception:
            self._emit(
                error_response(
                    request.id,
                    "INTERNAL_ERROR",
                    "The sidecar could not complete the request",
                )
            )
        finally:
            if self._active.get(request.id) is current:
                self._active.pop(request.id, None)

    async def _execute(self, request: Request) -> None:
        if request.method == "system.ping":
            self._emit(
                success_response(
                    request.id,
                    {
                        "status": "ok",
                        "protocol_version": 1,
                        "echo": request.params.get("echo"),
                    },
                )
            )
            return

        if self._enable_test_methods and request.method == "diagnostics.count":
            await self._count(request)
            return

        if self._enable_test_methods and request.method == "diagnostics.crash":
            exit_code = self._bounded_int(request.params, "exit_code", 1, 255, 70)
            os._exit(exit_code)

        self._emit(
            error_response(
                request.id, "METHOD_NOT_FOUND", f"Unknown method: {request.method}"
            )
        )

    async def _count(self, request: Request) -> None:
        steps = self._bounded_int(request.params, "steps", 1, 10_000, 5)
        delay_ms = self._bounded_int(request.params, "delay_ms", 0, 10_000, 25)
        for current in range(1, steps + 1):
            self._emit(
                event(
                    "request.progress",
                    {"request_id": request.id, "current": current, "total": steps},
                )
            )
            await asyncio.sleep(delay_ms / 1000)
        self._emit(success_response(request.id, {"completed_steps": steps}))

    async def _cancel(self, request: Request) -> None:
        target_id = request.params.get("request_id")
        if not isinstance(target_id, str) or not target_id:
            self._emit(
                error_response(
                    request.id, "INVALID_PARAMS", "request_id must be a non-empty string"
                )
            )
            return

        target = self._active.get(target_id)
        cancelled = target is not None and not target.done()
        if cancelled:
            target.cancel()
            await asyncio.sleep(0)
        self._emit(
            success_response(
                request.id, {"request_id": target_id, "cancelled": cancelled}
            )
        )

    @staticmethod
    def _bounded_int(
        params: Message, key: str, minimum: int, maximum: int, default: int
    ) -> int:
        value = params.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        if value < minimum or value > maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        return value
