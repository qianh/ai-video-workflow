"""Minimal UTC timestamp without importing persistence (avoid cycles)."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    )
