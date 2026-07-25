from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SIDECAR_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sidecar(tmp_path: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SIDECAR_ROOT / "src")
    env["WORKFLOW_SIDECAR_ENABLE_TEST_METHODS"] = "1"
    env["WORKFLOW_GLOBAL_DB"] = str(tmp_path / "global.db")
    process = subprocess.Popen(
        [sys.executable, "-m", "workflow_sidecar"],
        cwd=SIDECAR_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    yield process
    if process.poll() is None:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def send(process: subprocess.Popen[str], message: dict[str, object]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def receive(process: subprocess.Popen[str]) -> dict[str, object]:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line, "sidecar exited before producing a message"
    return json.loads(line)


def request(request_id: str, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "v": 1,
        "type": "request",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def test_stdio_stays_clean_across_1000_messages(sidecar: subprocess.Popen[str]) -> None:
    for index in range(1000):
        send(sidecar, request(f"ping_{index}", "system.ping", {"echo": index}))

    responses = [receive(sidecar) for _ in range(1000)]

    assert [message["id"] for message in responses] == [f"ping_{index}" for index in range(1000)]
    assert all(message["v"] == 1 and message["type"] == "response" for message in responses)
    assert sidecar.poll() is None


def test_stdio_reports_invalid_json_without_polluting_protocol(sidecar: subprocess.Popen[str]) -> None:
    assert sidecar.stdin is not None
    sidecar.stdin.write("not-json\n")
    sidecar.stdin.flush()

    error = receive(sidecar)
    send(sidecar, request("after_error", "system.ping"))
    response = receive(sidecar)

    assert error["error"]["code"] == "INVALID_JSON"
    assert response["id"] == "after_error"
    assert response["result"]["status"] == "ok"


def test_diagnostics_crash_exits_process(sidecar: subprocess.Popen[str]) -> None:
    send(sidecar, request("crash", "diagnostics.crash", {"exit_code": 73}))

    assert sidecar.wait(timeout=5) == 73
