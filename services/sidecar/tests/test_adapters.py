from __future__ import annotations

from pathlib import Path

from workflow_sidecar.adapters.base import MediaResult
from workflow_sidecar.adapters.ffmpeg_pipeline import (
    export_profile_copy,
    mux_still_with_audio,
    render_timeline_media,
    static_motion_from_image,
    write_placeholder_jpeg,
)
from workflow_sidecar.adapters.grok_media import generate_image
from workflow_sidecar.adapters.music_ytdlp import download_audio
from workflow_sidecar.adapters.policy import allow_mock
from workflow_sidecar.adapters.tts_mac import synthesize_speech
from workflow_sidecar.adapters.worker import JobWorker
from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.jobs import JobQueue


def test_allow_mock_under_pytest() -> None:
    assert allow_mock() is True


def test_placeholder_and_static_motion(tmp_path: Path) -> None:
    img = tmp_path / "a.jpg"
    r = write_placeholder_jpeg(img)
    assert r.ok and img.is_file()
    vid = tmp_path / "m.mp4"
    r2 = static_motion_from_image(img, vid, duration_sec=1.0)
    assert r2.ok and vid.is_file()


def test_tts_music_lipsync_render_export(tmp_path: Path) -> None:
    wav = tmp_path / "t.wav"
    assert synthesize_speech(wav, text="你好世界").ok
    music = tmp_path / "m.wav"
    assert download_audio(music, title="bed").ok
    lip = tmp_path / "lip.mp4"
    assert mux_still_with_audio(None, wav, lip, duration_sec=1.0).ok
    out = tmp_path / "r.mp4"
    assert render_timeline_media(out, stills=[], duration_sec=1.0, kind="proxy").ok
    exp = tmp_path / "e.mp4"
    assert export_profile_copy(out, exp, profile="master").ok


def test_generate_image_mock(tmp_path: Path) -> None:
    dest = tmp_path / "g.jpg"
    r = generate_image(dest, prompt="a girl in rain")
    assert r.ok and dest.is_file()
    assert r.mock is True or r.degraded is True


def test_media_result_dict() -> None:
    r = MediaResult(ok=True, adapter="x", output_path=Path("/tmp/a"), mime_type="a/b")
    d = r.to_dict()
    assert d["adapter"] == "x"
    assert d["output_path"] == "/tmp/a"


def test_job_worker_tick(tmp_path: Path) -> None:
    ws = WorkspaceService(tmp_path / "g.db")
    parent = tmp_path / "p"
    parent.mkdir()
    ws.create_project(parent, "W")
    q = JobQueue(ws.require_project_db())
    handled: list[str] = []

    def h(payload: dict) -> None:
        handled.append(payload.get("x", ""))

    worker = JobWorker(q, worker_id="w1", handlers={"demo.work": h})
    q.enqueue(kind="demo.work", payload={"x": "1"})
    import asyncio

    assert asyncio.run(worker.tick()) == 1
    assert handled == ["1"]
    assert worker.status()["worker_id"] == "w1"
    worker.start()
    worker.stop()
    ws.close()
