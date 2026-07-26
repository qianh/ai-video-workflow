from __future__ import annotations

import asyncio
from pathlib import Path

from workflow_sidecar.persistence import WorkspaceService
from workflow_sidecar.persistence.story import StoryService
from workflow_sidecar.protocol import Request
from workflow_sidecar.runtime import SidecarRuntime


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


SAMPLE = """# 第一章 夜市

女孩在雨中捡到发光的 U 盘。

# 第二章 追索

她发现 U 盘里藏着一段失踪消息。
"""


def test_import_split_and_events(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "global.db")
    parent = tmp_path / "projects"
    parent.mkdir()
    project = service.create_project(parent, "Story")
    story = StoryService(service.require_project_db(), Path(project.root_path))

    source = story.import_source(
        source_type="novel", title="夜市试播", text=SAMPLE
    )
    assert source.char_count == len(SAMPLE.replace("\r\n", "\n"))
    text_path = Path(project.root_path) / source.text_path
    assert text_path.is_file()
    # Original text must not be rewritten by later AI steps (file preserved).
    original = text_path.read_text(encoding="utf-8")

    chunks = story.split_chapters(source.id)
    assert len(chunks) >= 2
    assert chunks[0].title and "夜市" in (chunks[0].title or "")
    assert chunks[0].char_end > chunks[0].char_start
    assert text_path.read_text(encoding="utf-8") == original

    span_start = original.find("发光的 U 盘")
    span_end = span_start + len("发光的 U 盘")
    event = story.create_event(
        title="发现 U 盘",
        summary="雨夜拾获发光 U 盘",
        order_key=1,
        origin="extracted",
        story_source_id=source.id,
        char_start=span_start,
        char_end=span_end,
    )
    assert event.quote_hash
    assert event.source_chunk_id is not None

    creative = story.create_event(
        title="补充动机",
        summary="她想查清失踪真相",
        order_key=2,
        origin="creative",
    )
    assert creative.origin == "creative"
    assert creative.story_source_id is None

    edge = story.create_edge(
        from_event_id=event.event_id,
        to_event_id=creative.event_id,
        relation="enables",
    )
    assert edge["relation"] == "enables"
    assert len(story.list_events()) == 2
    assert len(story.list_edges()) == 1
    service.close()


def test_story_rpc_flow(tmp_path: Path) -> None:
    async def scenario() -> dict[str, dict[str, object]]:
        messages: list[dict[str, object]] = []
        runtime = SidecarRuntime(
            messages.append, global_db_path=tmp_path / "g.db"
        )
        parent = tmp_path / "p"
        parent.mkdir()
        await runtime.handle(
            Request(
                "c",
                "project.create",
                {"parent_dir": str(parent), "name": "M2"},
            )
        )
        await runtime.handle(
            Request(
                "imp",
                "story.import_source",
                {
                    "source_type": "novel",
                    "title": "样例",
                    "text": SAMPLE,
                },
            )
        )
        source_id = next(m for m in messages if m["id"] == "imp")["result"]["id"]
        await runtime.handle(
            Request("split", "story.split_chapters", {"source_id": source_id})
        )
        await runtime.handle(Request("sources", "story.list_sources", {}))
        await runtime.handle(
            Request("chunks", "story.list_chunks", {"source_id": source_id})
        )
        text = SAMPLE
        start = text.find("失踪消息")
        end = start + len("失踪消息")
        await runtime.handle(
            Request(
                "evt",
                "story.create_event",
                {
                    "title": "失踪线索",
                    "summary": "U 盘里有失踪消息",
                    "order_key": 1,
                    "origin": "extracted",
                    "story_source_id": source_id,
                    "char_start": start,
                    "char_end": end,
                },
            )
        )
        await runtime.handle(Request("events", "story.list_events", {}))
        await runtime.shutdown()
        return {str(m["id"]): m for m in messages if "id" in m}

    by_id = run(scenario())
    assert by_id["imp"]["result"]["status"] == "imported"
    assert len(by_id["split"]["result"]["chunks"]) >= 2
    assert by_id["sources"]["result"]["sources"]
    assert by_id["evt"]["result"]["origin"] == "extracted"
    assert by_id["events"]["result"]["events"]
