"""When mock media is allowed vs real execution required."""

from __future__ import annotations

import os


def allow_mock() -> bool:
    """Return True only for explicit test/dev mock mode.

    Production default is real tools; missing tools degrade with status flags.
    """
    for key in ("WORKFLOW_ALLOW_MOCK", "WORKFLOW_ACCEPTANCE_FAST"):
        if os.environ.get(key, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    # pytest runs without forcing every machine to have grok auth
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return False


def mock_reason() -> str:
    if os.environ.get("WORKFLOW_ACCEPTANCE_FAST", "").strip():
        return "WORKFLOW_ACCEPTANCE_FAST"
    if os.environ.get("WORKFLOW_ALLOW_MOCK", "").strip():
        return "WORKFLOW_ALLOW_MOCK"
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "pytest"
    return "explicit_mock"
