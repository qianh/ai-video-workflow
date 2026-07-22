#!/usr/bin/env python3
"""M0-04: verify Codex CLI non-interactive structured output.

Runs `codex exec` repeatedly with --output-schema and validates each last
message against a fixed JSON Schema. Acceptance: 10 consecutive valid runs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "spikes" / "m0-04" / "shot_card.schema.json"
DEFAULT_OUT_DIR = ROOT / "artifacts" / "m0-04"


@dataclass
class AttemptResult:
    index: int
    ok: bool
    elapsed_sec: float
    exit_code: int
    output_path: str
    error: str | None = None
    payload: dict[str, Any] | None = None


def validate_against_schema(payload: Any, schema: dict[str, Any]) -> None:
    """Minimal Draft-2020-12 subset validator for the fixed shot-card schema."""

    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    if schema.get("type") != "object":
        raise ValueError("schema must describe an object")

    required = schema.get("required", [])
    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties", True)

    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"missing required fields: {missing}")

    if additional is False:
        extra = [key for key in payload if key not in properties]
        if extra:
            raise ValueError(f"unexpected fields: {extra}")

    for key, value in payload.items():
        prop = properties.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        if expected == "string":
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if "minLength" in prop and len(value) < prop["minLength"]:
                raise ValueError(f"{key} shorter than minLength")
            if "maxLength" in prop and len(value) > prop["maxLength"]:
                raise ValueError(f"{key} longer than maxLength")
            if "enum" in prop and value not in prop["enum"]:
                raise ValueError(f"{key} not in enum")
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
            number = float(value)
            if "minimum" in prop and number < prop["minimum"]:
                raise ValueError(f"{key} below minimum")
            if "maximum" in prop and number > prop["maximum"]:
                raise ValueError(f"{key} above maximum")
        else:
            raise ValueError(f"unsupported schema type for {key}: {expected}")


def resolve_codex(binary: str | None) -> str:
    if binary:
        return binary
    found = shutil.which("codex")
    if not found:
        raise FileNotFoundError("codex CLI not found on PATH")
    return found


def run_once(
    *,
    index: int,
    codex: str,
    schema_path: Path,
    out_dir: Path,
    model: str | None,
    prompt: str,
    timeout_sec: int,
) -> AttemptResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    message_path = out_dir / f"run-{index:02d}.json"
    stdout_path = out_dir / f"run-{index:02d}.stdout"
    stderr_path = out_dir / f"run-{index:02d}.stderr"
    if message_path.exists():
        message_path.unlink()

    command = [
        codex,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(message_path),
        "-C",
        str(ROOT),
    ]
    if model:
        command.extend(["--model", model])
    command.append(prompt)

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        stdout_path.write_text(exc.stdout or "", encoding="utf-8")
        stderr_path.write_text(exc.stderr or "", encoding="utf-8")
        return AttemptResult(
            index=index,
            ok=False,
            elapsed_sec=elapsed,
            exit_code=124,
            output_path=str(message_path),
            error=f"timeout after {timeout_sec}s",
        )

    elapsed = time.perf_counter() - started
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")

    if completed.returncode != 0:
        return AttemptResult(
            index=index,
            ok=False,
            elapsed_sec=elapsed,
            exit_code=completed.returncode,
            output_path=str(message_path),
            error=f"codex exit {completed.returncode}",
        )

    if not message_path.is_file():
        return AttemptResult(
            index=index,
            ok=False,
            elapsed_sec=elapsed,
            exit_code=completed.returncode,
            output_path=str(message_path),
            error="missing --output-last-message file",
        )

    raw = message_path.read_text(encoding="utf-8").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return AttemptResult(
            index=index,
            ok=False,
            elapsed_sec=elapsed,
            exit_code=completed.returncode,
            output_path=str(message_path),
            error=f"invalid JSON: {exc}",
        )

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        validate_against_schema(payload, schema)
    except ValueError as exc:
        return AttemptResult(
            index=index,
            ok=False,
            elapsed_sec=elapsed,
            exit_code=completed.returncode,
            output_path=str(message_path),
            error=f"schema validation failed: {exc}",
            payload=payload if isinstance(payload, dict) else None,
        )

    return AttemptResult(
        index=index,
        ok=True,
        elapsed_sec=elapsed,
        exit_code=completed.returncode,
        output_path=str(message_path),
        payload=payload,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--codex", type=str, default=None)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument(
        "--prompt",
        type=str,
        default=(
            "Return exactly one JSON object matching the provided schema. "
            "Invent a short manhua shot card for: a girl finds a glowing USB "
            "in a rainy night market. No markdown, no commentary."
        ),
    )
    args = parser.parse_args()

    if args.runs < 1:
        print("runs must be >= 1", file=sys.stderr)
        return 2

    codex = resolve_codex(args.codex)
    schema_path = args.schema.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[AttemptResult] = []
    consecutive_ok = 0
    for index in range(1, args.runs + 1):
        print(f"[m0-04] run {index}/{args.runs} …", flush=True)
        result = run_once(
            index=index,
            codex=codex,
            schema_path=schema_path,
            out_dir=out_dir,
            model=args.model,
            prompt=args.prompt,
            timeout_sec=args.timeout_sec,
        )
        results.append(result)
        status = "ok" if result.ok else f"FAIL ({result.error})"
        print(f"[m0-04] run {index}: {status} in {result.elapsed_sec:.1f}s", flush=True)
        if result.ok:
            consecutive_ok += 1
        else:
            break

    summary = {
        "task": "M0-04",
        "codex": codex,
        "codex_version": subprocess.run(
            [codex, "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
        or subprocess.run(
            [codex, "--version"], capture_output=True, text=True, check=False
        ).stderr.strip(),
        "schema": str(schema_path),
        "requested_runs": args.runs,
        "completed_runs": len(results),
        "consecutive_ok": consecutive_ok,
        "accepted": consecutive_ok >= args.runs,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "attempts": [asdict(item) for item in results],
        "command_template": [
            "codex",
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            "<run-N.json>",
            "-C",
            str(ROOT),
            "<prompt>",
        ],
        "recommended_adapter_mapping": {
            "capability": "text.structured_generate",
            "cli": "codex exec",
            "structured_output": "--output-schema + --output-last-message",
            "isolation": "--ephemeral + task workdir + read-only sandbox",
        },
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"accepted": summary["accepted"], "consecutive_ok": consecutive_ok}, indent=2))
    print(f"summary: {summary_path}")
    return 0 if summary["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
