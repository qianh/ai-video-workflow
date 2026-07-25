"""Request dispatch and cancellation for the workflow sidecar."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
from typing import Any

from .persistence import JobQueue, WorkspaceService
from .protocol import Request, error_response, event, success_response


Message = dict[str, Any]
Emitter = Callable[[Message], None]


def default_global_db_path() -> Path:
    override = os.environ.get("WORKFLOW_GLOBAL_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ai-video-workflow" / "global.db"


class SidecarRuntime:
    """Runs requests without coupling protocol transport to business handlers."""

    def __init__(
        self,
        emit: Emitter,
        *,
        enable_test_methods: bool = False,
        global_db_path: Path | None = None,
    ) -> None:
        self._emit = emit
        self._enable_test_methods = enable_test_methods
        self._active: dict[str, asyncio.Task[None]] = {}
        self._workspace = WorkspaceService(global_db_path or default_global_db_path())

    async def shutdown(self) -> None:
        self._workspace.close()

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

        if request.method == "project.create":
            parent_dir = request.params.get("parent_dir")
            name = request.params.get("name")
            if not isinstance(parent_dir, str) or not parent_dir:
                raise ValueError("parent_dir must be a non-empty string")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("name must be a non-empty string")
            record = self._workspace.create_project(parent_dir, name)
            self._emit(success_response(request.id, record.as_dict()))
            return

        if request.method == "project.open":
            root_dir = request.params.get("root_dir")
            if not isinstance(root_dir, str) or not root_dir:
                raise ValueError("root_dir must be a non-empty string")
            record = self._workspace.open_project(root_dir)
            self._emit(success_response(request.id, record.as_dict()))
            return

        if request.method == "project.close":
            previous = self._workspace.close_project()
            self._emit(
                success_response(
                    request.id,
                    {"closed": previous.as_dict() if previous else None},
                )
            )
            return

        if request.method == "project.current":
            current = self._workspace.current
            self._emit(
                success_response(
                    request.id,
                    {"project": current.as_dict() if current else None},
                )
            )
            return

        if request.method == "project.list_recent":
            limit = request.params.get("limit", 20)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            projects = self._workspace.list_recent(limit)
            self._emit(
                success_response(
                    request.id,
                    {"projects": [item.as_dict() for item in projects]},
                )
            )
            return

        if request.method.startswith("job."):
            await self._execute_job(request)
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

    def _jobs(self) -> JobQueue:
        return JobQueue(self._workspace.require_project_db())

    async def _execute_job(self, request: Request) -> None:
        queue = self._jobs()
        method = request.method
        params = request.params

        if method == "job.enqueue":
            kind = params.get("kind")
            if not isinstance(kind, str):
                raise ValueError("kind must be a string")
            payload = params.get("payload") or {}
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            max_attempts = params.get("max_attempts", 3)
            if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
                raise ValueError("max_attempts must be an integer")
            job = queue.enqueue(kind, payload, max_attempts=max_attempts)
            self._emit(success_response(request.id, job.as_dict()))
            return

        if method == "job.get":
            job_id = params.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            self._emit(success_response(request.id, queue.get(job_id).as_dict()))
            return

        if method == "job.list":
            status = params.get("status")
            if status is not None and not isinstance(status, str):
                raise ValueError("status must be a string")
            limit = params.get("limit", 50)
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            jobs = queue.list(status=status, limit=limit)
            self._emit(
                success_response(
                    request.id, {"jobs": [item.as_dict() for item in jobs]}
                )
            )
            return

        if method == "job.claim":
            worker_id = params.get("worker_id")
            if not isinstance(worker_id, str) or not worker_id:
                raise ValueError("worker_id must be a non-empty string")
            lease_seconds = params.get("lease_seconds", 60)
            if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
                raise ValueError("lease_seconds must be an integer")
            kinds = params.get("kinds")
            if kinds is not None:
                if not isinstance(kinds, list) or not all(
                    isinstance(item, str) for item in kinds
                ):
                    raise ValueError("kinds must be a string array")
            job = queue.claim(
                worker_id, lease_seconds=lease_seconds, kinds=kinds
            )
            self._emit(
                success_response(
                    request.id, {"job": job.as_dict() if job else None}
                )
            )
            return

        if method == "job.complete":
            job_id = params.get("job_id")
            worker_id = params.get("worker_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            if not isinstance(worker_id, str) or not worker_id:
                raise ValueError("worker_id must be a non-empty string")
            self._emit(
                success_response(
                    request.id, queue.complete(job_id, worker_id).as_dict()
                )
            )
            return

        if method == "job.fail":
            job_id = params.get("job_id")
            worker_id = params.get("worker_id")
            error = params.get("error", "failed")
            retry = params.get("retry", True)
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            if not isinstance(worker_id, str) or not worker_id:
                raise ValueError("worker_id must be a non-empty string")
            if not isinstance(error, str):
                raise ValueError("error must be a string")
            if not isinstance(retry, bool):
                raise ValueError("retry must be a boolean")
            self._emit(
                success_response(
                    request.id,
                    queue.fail(job_id, worker_id, error, retry=retry).as_dict(),
                )
            )
            return

        if method == "job.cancel":
            job_id = params.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            self._emit(success_response(request.id, queue.cancel(job_id).as_dict()))
            return

        if method == "job.pause":
            job_id = params.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            self._emit(success_response(request.id, queue.pause(job_id).as_dict()))
            return

        if method == "job.resume":
            job_id = params.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("job_id must be a non-empty string")
            self._emit(success_response(request.id, queue.resume(job_id).as_dict()))
            return

        if method == "job.reclaim_expired":
            count = queue.reclaim_expired()
            self._emit(success_response(request.id, {"reclaimed": count}))
            return

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
