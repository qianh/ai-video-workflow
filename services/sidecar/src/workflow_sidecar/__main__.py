"""Run the workflow sidecar over stdin/stdout."""

from __future__ import annotations

import asyncio

from .server import serve


if __name__ == "__main__":
    asyncio.run(serve())
