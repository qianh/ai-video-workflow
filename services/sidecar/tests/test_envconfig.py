from __future__ import annotations

from pathlib import Path

from workflow_sidecar.persistence.envconfig import (
    merge_env,
    parse_dotenv,
    redact_mapping,
    resolve_task_env,
    summarize_env,
)


def test_parse_dotenv_supports_export_and_quotes() -> None:
    text = """
    # comment
    export FOO=bar
    NAME="hello world"
    EMPTY=
    BADLINE
    """
    assert parse_dotenv(text) == {"FOO": "bar", "NAME": "hello world", "EMPTY": ""}


def test_merge_priority_project_over_global_over_process() -> None:
    merged = merge_env(
        process_env={"A": "p", "B": "p", "C": "p"},
        global_env={"B": "g", "C": "g"},
        project_env={"C": "proj"},
    )
    assert merged == {"A": "p", "B": "g", "C": "proj"}


def test_resolve_and_summary_hide_secrets(tmp_path: Path) -> None:
    global_env = tmp_path / "env"
    global_env.write_text("PUBLIC=1\nAPI_KEY=from-global\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env.local").write_text(
        "API_KEY=from-project\nNOTE=ok\n", encoding="utf-8"
    )

    resolved = resolve_task_env(
        project_root=project,
        global_env_path=global_env,
        process_env={"PATH": "/bin", "TOKEN": "proc"},
    )
    assert resolved["API_KEY"] == "from-project"
    assert resolved["PUBLIC"] == "1"
    assert resolved["TOKEN"] == "proc"

    allowed = resolve_task_env(
        project_root=project,
        global_env_path=global_env,
        process_env={"PATH": "/bin", "TOKEN": "proc"},
        allow_keys=["NOTE", "MISSING"],
    )
    assert allowed == {"NOTE": "ok"}

    summary = summarize_env(
        project_root=project,
        global_env_path=global_env,
        process_env={"PATH": "/bin", "TOKEN": "proc"},
        keys=["API_KEY", "NOTE", "PUBLIC", "TOKEN", "MISSING"],
    )
    by_key = {item.key: item for item in summary}
    assert by_key["API_KEY"].source == "project"
    assert by_key["API_KEY"].is_secret is True
    assert by_key["API_KEY"].set is True
    assert by_key["NOTE"].source == "project"
    assert by_key["PUBLIC"].source == "global"
    assert by_key["TOKEN"].source == "process"
    assert by_key["MISSING"].set is False

    redacted = redact_mapping(resolved)
    assert redacted["API_KEY"] == "<redacted>"
    assert redacted["NOTE"] == "ok"
