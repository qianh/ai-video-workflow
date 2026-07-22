from __future__ import annotations

import asyncio

from workflow_sidecar.server import serve


if __name__ == "__main__":
    asyncio.run(serve())
