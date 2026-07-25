"""Environment merge for tasks (M1-07 / ADR security rules).

Priority (highest wins): project `.env.local` → app global env file → process env.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

SECRET_KEY_RE = re.compile(
    r"(secret|token|password|passwd|api[_-]?key|access[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EnvBinding:
    key: str
    source: str  # project | global | process
    is_secret: bool
    set: bool


def is_secret_key(key: str) -> bool:
    return SECRET_KEY_RE.search(key) is not None


def parse_dotenv(text: str) -> dict[str, str]:
    """Minimal dotenv parser: KEY=VALUE, ignores comments/blank lines."""

    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[key] = value
    return result


def load_dotenv_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_dotenv(path.read_text(encoding="utf-8"))


def default_global_env_path(global_db_path: Path) -> Path:
    return global_db_path.parent / "env"


def merge_env(
    *,
    process_env: Mapping[str, str],
    global_env: Mapping[str, str],
    project_env: Mapping[str, str],
) -> dict[str, str]:
    """Lower layers first; higher priority overwrites."""

    merged = dict(process_env)
    merged.update(global_env)
    merged.update(project_env)
    return merged


def resolve_task_env(
    *,
    project_root: Path | None,
    global_env_path: Path,
    process_env: Mapping[str, str] | None = None,
    allow_keys: list[str] | None = None,
) -> dict[str, str]:
    process = dict(process_env if process_env is not None else os.environ)
    global_values = load_dotenv_file(global_env_path)
    project_values: dict[str, str] = {}
    if project_root is not None:
        project_values = load_dotenv_file(project_root / ".env.local")
    merged = merge_env(
        process_env=process,
        global_env=global_values,
        project_env=project_values,
    )
    if allow_keys is not None:
        allowed = set(allow_keys)
        return {key: value for key, value in merged.items() if key in allowed}
    return merged


def summarize_env(
    *,
    project_root: Path | None,
    global_env_path: Path,
    process_env: Mapping[str, str] | None = None,
    keys: list[str] | None = None,
) -> list[EnvBinding]:
    """UI-safe view: never returns secret values."""

    process = dict(process_env if process_env is not None else os.environ)
    global_values = load_dotenv_file(global_env_path)
    project_values: dict[str, str] = {}
    if project_root is not None:
        project_values = load_dotenv_file(project_root / ".env.local")

    if keys is None:
        keys = sorted(
            set(process) | set(global_values) | set(project_values),
            key=str.lower,
        )

    bindings: list[EnvBinding] = []
    for key in keys:
        if key in project_values:
            source = "project"
            present = True
        elif key in global_values:
            source = "global"
            present = True
        elif key in process:
            source = "process"
            present = True
        else:
            source = "process"
            present = False
        bindings.append(
            EnvBinding(
                key=key,
                source=source,
                is_secret=is_secret_key(key),
                set=present,
            )
        )
    return bindings


def redact_mapping(values: Mapping[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in values.items():
        if is_secret_key(key):
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted
