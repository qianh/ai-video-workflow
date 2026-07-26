"""JSONL logging with secret redaction before write."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from ..persistence.timeutil import utc_now

# Bearer/token-like values and common key=value secrets.
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer <redacted>"),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9]{10,}"), "sk-<redacted>"),
    (re.compile(r"(?i)\bxai-[A-Za-z0-9]{10,}"), "xai-<redacted>"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|cookie|authorization)\b\s*[:=]\s*([^\s,\"';]+)"
        ),
        r"\1=<redacted>",
    ),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in _REDACT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        key_l = str(key).lower()
        if any(
            token in key_l
            for token in ("secret", "token", "password", "passwd", "api_key", "apikey", "cookie")
        ):
            result[key] = "<redacted>"
        elif isinstance(value, str):
            result[key] = redact_text(value)
        elif isinstance(value, Mapping):
            result[key] = redact_mapping(value)
        else:
            result[key] = value
    return result


class JsonlLogger:
    """Append-only JSON Lines logger. Never writes unredacted secrets."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        level: str,
        message: str,
        *,
        fields: Mapping[str, Any] | None = None,
        diagnostic_id: str | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": utc_now(),
            "level": level.upper(),
            "message": redact_text(message),
        }
        if fields:
            record["fields"] = redact_mapping(fields)
        if diagnostic_id:
            record["diagnostic_id"] = diagnostic_id
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")
        return record

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        selected = lines[-limit:]
        result: list[dict[str, Any]] = []
        for line in selected:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                result.append(payload)
        return result
