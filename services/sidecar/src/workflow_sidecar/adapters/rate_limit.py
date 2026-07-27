"""Grok / paid-CLI rate limit and session cost control."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class RateLimitState:
    calls: int = 0
    blocked: int = 0
    last_call_at: float = 0.0
    total_wait_sec: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


_STATE = RateLimitState()


def min_interval_sec() -> float:
    ms = _env_int("WORKFLOW_GROK_MIN_INTERVAL_MS", 1500)
    return max(0.0, ms / 1000.0)


def max_calls_per_session() -> int:
    return max(0, _env_int("WORKFLOW_GROK_MAX_CALLS", 50))


def acquire_grok_slot(*, tool: str = "image_gen") -> dict[str, Any]:
    """Block until rate window allows a call, or raise if budget exhausted."""
    interval = min_interval_sec()
    budget = max_calls_per_session()
    with _STATE.lock:
        if budget > 0 and _STATE.calls >= budget:
            _STATE.blocked += 1
            raise RuntimeError(
                f"Grok session budget exhausted ({_STATE.calls}/{budget}). "
                "Raise WORKFLOW_GROK_MAX_CALLS or restart sidecar."
            )
        now = time.monotonic()
        wait = 0.0
        if _STATE.last_call_at > 0 and interval > 0:
            wait = max(0.0, interval - (now - _STATE.last_call_at))
        if wait > 0:
            time.sleep(wait)
            _STATE.total_wait_sec += wait
            now = time.monotonic()
        _STATE.last_call_at = now
        _STATE.calls += 1
        return {
            "tool": tool,
            "call_no": _STATE.calls,
            "budget": budget,
            "waited_sec": round(wait, 3),
            "min_interval_sec": interval,
        }


def rate_limit_status() -> dict[str, Any]:
    with _STATE.lock:
        return {
            "calls": _STATE.calls,
            "blocked": _STATE.blocked,
            "budget": max_calls_per_session(),
            "min_interval_ms": int(min_interval_sec() * 1000),
            "total_wait_sec": round(_STATE.total_wait_sec, 3),
        }


def reset_for_tests() -> None:
    with _STATE.lock:
        _STATE.calls = 0
        _STATE.blocked = 0
        _STATE.last_call_at = 0.0
        _STATE.total_wait_sec = 0.0
