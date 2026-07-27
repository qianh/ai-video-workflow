"""Background job worker: claim durable jobs and run media kinds."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..persistence.jobs import JobQueue

logger = logging.getLogger(__name__)

Message = dict[str, Any]
Emitter = Callable[[Message], None]


class JobWorker:
    """Simple in-process claim loop for long-running media work."""

    def __init__(
        self,
        queue: JobQueue,
        *,
        worker_id: str = "sidecar-worker-1",
        emit: Emitter | None = None,
        handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    ) -> None:
        self._queue = queue
        self._worker_id = worker_id
        self._emit = emit
        self._handlers = handlers or {}
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "worker_id": self._worker_id,
            "handlers": sorted(self._handlers),
        }

    def register(self, kind: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        self._handlers[kind] = handler

    def start(self) -> dict[str, Any]:
        if self._running:
            return self.status()
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._loop())
        except RuntimeError:
            # no loop yet — caller may drive tick()
            self._task = None
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
        return self.status()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception:  # pragma: no cover
                logger.exception("worker tick failed")
            await asyncio.sleep(0.5)

    async def tick(self) -> int:
        """Claim and process up to one job. Returns 1 if worked, else 0."""
        job = self._queue.claim(self._worker_id, kinds=None, lease_seconds=120)
        if job is None:
            return 0
        handler = self._handlers.get(job.kind)
        try:
            if handler is None:
                raise ValueError(f"no handler for kind {job.kind}")
            if self._emit:
                self._emit(
                    {
                        "type": "event",
                        "event": "job.progress",
                        "data": {
                            "job_id": job.id,
                            "kind": job.kind,
                            "status": "running",
                            "worker_id": self._worker_id,
                        },
                    }
                )
            result = handler(job.payload if isinstance(job.payload, dict) else {})
            if asyncio.iscoroutine(result):
                await result
            self._queue.complete(job.id, self._worker_id)
            if self._emit:
                self._emit(
                    {
                        "type": "event",
                        "event": "job.progress",
                        "data": {
                            "job_id": job.id,
                            "kind": job.kind,
                            "status": "succeeded",
                            "worker_id": self._worker_id,
                        },
                    }
                )
        except Exception as exc:
            self._queue.fail(job.id, self._worker_id, error=str(exc)[:1000])
            if self._emit:
                self._emit(
                    {
                        "type": "event",
                        "event": "job.progress",
                        "data": {
                            "job_id": job.id,
                            "kind": job.kind,
                            "status": "failed",
                            "error": str(exc)[:300],
                            "worker_id": self._worker_id,
                        },
                    }
                )
        return 1
