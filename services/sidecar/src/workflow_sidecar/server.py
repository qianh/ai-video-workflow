"""stdio transport for the versioned NDJSON sidecar protocol."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from .protocol import ProtocolError, decode_request, error_response
from .runtime import SidecarRuntime


def emit(message: dict[str, Any]) -> None:
    """Write exactly one protocol message to stdout."""

    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


async def serve() -> None:
    runtime = SidecarRuntime(
        emit,
        enable_test_methods=os.environ.get("WORKFLOW_SIDECAR_ENABLE_TEST_METHODS") == "1",
    )
    tasks: set[asyncio.Task[None]] = set()

    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            break
        try:
            request = decode_request(line.rstrip(b"\r\n"))
        except ProtocolError as exc:
            emit(error_response(exc.request_id, exc.code, exc.message))
            continue

        task = asyncio.create_task(runtime.handle(request))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    if tasks:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    await runtime.shutdown()
