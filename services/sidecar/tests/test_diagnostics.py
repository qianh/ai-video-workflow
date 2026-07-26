from __future__ import annotations

import json
import zipfile
from pathlib import Path

from workflow_sidecar.diagnostics import JsonlLogger, create_diagnostic_pack, redact_text
from workflow_sidecar.diagnostics.pack import default_log_path


def test_redact_text_masks_tokens_and_keys() -> None:
    text = "Authorization: Bearer abc.def.ghi api_key=supersecret sk-abcdefghijklmnop"
    redacted = redact_text(text)
    assert "supersecret" not in redacted
    assert "abc.def.ghi" not in redacted
    assert "sk-abcdefghijklmnop" not in redacted
    assert "<redacted>" in redacted


def test_jsonl_logger_redacts_on_write(tmp_path: Path) -> None:
    logger = JsonlLogger(tmp_path / "app.jsonl")
    record = logger.write(
        "info",
        "login cookie=abc123",
        fields={"api_key": "xyz", "note": "ok"},
    )
    assert "abc123" not in record["message"]
    assert record["fields"]["api_key"] == "<redacted>"
    assert record["fields"]["note"] == "ok"
    tail = logger.tail(10)
    assert len(tail) == 1
    assert "xyz" not in json.dumps(tail[0])


def test_diagnostic_pack_excludes_env_local(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text('{"name":"demo"}\n', encoding="utf-8")
    (project / ".env.local").write_text("API_KEY=should-not-pack\n", encoding="utf-8")
    log_path = project / "logs" / "sidecar.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        json.dumps({"message": "token=leak-me"}) + "\n", encoding="utf-8"
    )

    pack = create_diagnostic_pack(
        output_dir=tmp_path / "out",
        project_root=project,
        project_schema_version=1,
        job_summary=[{"id": "j1", "status": "queued"}],
        capability_status={"ffmpeg": "ready"},
        log_path=log_path,
    )
    assert Path(pack.path).is_file()
    with zipfile.ZipFile(pack.path) as archive:
        names = set(archive.namelist())
        assert ".env.local" not in names
        assert "meta.json" in names
        assert "jobs.json" in names
        assert "logs-tail.jsonl" in names
        log_tail = archive.read("logs-tail.jsonl").decode("utf-8")
        assert "leak-me" not in log_tail

    assert default_log_path(project_root=project, global_db_path=tmp_path / "g.db").name == (
        "sidecar.jsonl"
    )
