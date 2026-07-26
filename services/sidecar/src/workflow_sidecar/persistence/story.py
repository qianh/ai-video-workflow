"""Story source import, chapter split, and narrative event graph (M2-01/02)."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .database import Database
from .paths import resolve_project_path
from .timeutil import utc_now

SOURCE_TYPES = frozenset(
    {"idea", "novel", "season_outline", "episode_scripts", "mixed"}
)
EDGE_RELATIONS = frozenset(
    {"precedes", "causes", "enables", "conflicts", "foreshadows"}
)
CHAPTER_HEADING = re.compile(
    r"^(?:#{1,3}\s+|(?:第[一二三四五六七八九十百千0-9]+[章节回部]\s*)).+",
    re.MULTILINE,
)


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class StorySourceRecord:
    id: str
    source_type: str
    title: str
    status: str
    text_path: str
    content_hash: str
    char_count: int
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceChunkRecord:
    id: str
    story_source_id: str
    parent_chunk_id: str | None
    chunk_type: str
    ordinal: int
    title: str | None
    char_start: int
    char_end: int
    content_hash: str
    split_batch_id: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NarrativeEventView:
    event_id: str
    revision_id: str
    branch_id: str
    title: str
    summary: str
    order_key: float
    origin: str
    status: str
    story_source_id: str | None
    source_chunk_id: str | None
    char_start: int | None
    char_end: int | None
    quote_hash: str | None
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class StoryService:
    def __init__(self, db: Database, project_root: Path) -> None:
        self._db = db
        self._root = project_root.resolve()
        self._ensure_primary_branch()

    def _ensure_primary_branch(self) -> str:
        row = self._db.fetchone(
            "SELECT id FROM story_branches WHERE is_primary = 1 LIMIT 1"
        )
        if row is not None:
            return str(row["id"])
        branch_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO story_branches(id, name, parent_branch_id, is_primary, status, created_at, updated_at)
            VALUES (?, '主线', NULL, 1, 'primary', ?, ?)
            """,
            (branch_id, now, now),
        )
        self._db.commit()
        return branch_id

    def primary_branch_id(self) -> str:
        return self._ensure_primary_branch()

    def import_source(
        self,
        *,
        source_type: str,
        title: str,
        text: str,
    ) -> StorySourceRecord:
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"invalid source_type: {source_type}")
        safe_title = title.strip()
        if not safe_title:
            raise ValueError("title must be a non-empty string")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")

        normalized = normalize_text(text)
        source_id = str(uuid.uuid4())
        now = utc_now()
        relative = f"sources/normalized/{source_id}.txt"
        absolute = resolve_project_path(self._root, relative)
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(normalized, encoding="utf-8")
        digest = content_hash(normalized)
        record = StorySourceRecord(
            id=source_id,
            source_type=source_type,
            title=safe_title,
            status="imported",
            text_path=relative,
            content_hash=digest,
            char_count=len(normalized),
            created_at=now,
            updated_at=now,
        )
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO story_sources(
                    id, source_type, title, status, text_path, content_hash,
                    char_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.source_type,
                    record.title,
                    record.status,
                    record.text_path,
                    record.content_hash,
                    record.char_count,
                    record.created_at,
                    record.updated_at,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return record

    def list_sources(self) -> list[StorySourceRecord]:
        rows = self._db.fetchall(
            "SELECT * FROM story_sources ORDER BY created_at DESC"
        )
        return [_row_source(row) for row in rows]

    def get_source_text(self, source_id: str) -> str:
        source = self.get_source(source_id)
        path = resolve_project_path(self._root, source.text_path)
        return path.read_text(encoding="utf-8")

    def get_source(self, source_id: str) -> StorySourceRecord:
        row = self._db.fetchone(
            "SELECT * FROM story_sources WHERE id = ?", (source_id,)
        )
        if row is None:
            raise ValueError(f"story source not found: {source_id}")
        return _row_source(row)

    def split_chapters(self, source_id: str) -> list[SourceChunkRecord]:
        source = self.get_source(source_id)
        text = self.get_source_text(source_id)
        batch_id = str(uuid.uuid4())
        now = utc_now()
        chunks = _split_into_chunks(text)
        records: list[SourceChunkRecord] = []
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            # New split batch does not mutate previous chunks.
            for ordinal, (title, start, end) in enumerate(chunks):
                segment = text[start:end]
                chunk_id = str(uuid.uuid4())
                record = SourceChunkRecord(
                    id=chunk_id,
                    story_source_id=source_id,
                    parent_chunk_id=None,
                    chunk_type="chapter",
                    ordinal=ordinal,
                    title=title,
                    char_start=start,
                    char_end=end,
                    content_hash=content_hash(segment),
                    split_batch_id=batch_id,
                    created_at=now,
                )
                self._db.execute(
                    """
                    INSERT INTO source_chunks(
                        id, story_source_id, parent_chunk_id, chunk_type, ordinal,
                        title, char_start, char_end, content_hash, split_batch_id, created_at
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.story_source_id,
                        record.chunk_type,
                        record.ordinal,
                        record.title,
                        record.char_start,
                        record.char_end,
                        record.content_hash,
                        record.split_batch_id,
                        record.created_at,
                    ),
                )
                records.append(record)
            self._db.execute(
                """
                UPDATE story_sources
                SET status = 'split', updated_at = ?
                WHERE id = ?
                """,
                (now, source_id),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return records

    def list_chunks(self, source_id: str, *, latest_only: bool = True) -> list[SourceChunkRecord]:
        self.get_source(source_id)
        if latest_only:
            row = self._db.fetchone(
                """
                SELECT split_batch_id FROM source_chunks
                WHERE story_source_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (source_id,),
            )
            if row is None:
                return []
            batch_id = row["split_batch_id"]
            rows = self._db.fetchall(
                """
                SELECT * FROM source_chunks
                WHERE story_source_id = ? AND split_batch_id = ?
                ORDER BY ordinal ASC
                """,
                (source_id, batch_id),
            )
        else:
            rows = self._db.fetchall(
                """
                SELECT * FROM source_chunks
                WHERE story_source_id = ?
                ORDER BY created_at ASC, ordinal ASC
                """,
                (source_id,),
            )
        return [_row_chunk(row) for row in rows]

    def create_event(
        self,
        *,
        title: str,
        summary: str,
        order_key: float,
        origin: str = "extracted",
        story_source_id: str | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        confidence: float = 1.0,
    ) -> NarrativeEventView:
        safe_title = title.strip()
        safe_summary = summary.strip()
        if not safe_title or not safe_summary:
            raise ValueError("title and summary are required")
        if origin not in {"extracted", "creative"}:
            raise ValueError("origin must be extracted or creative")
        if origin == "extracted":
            if not story_source_id:
                raise ValueError("extracted events require story_source_id")
            if char_start is None or char_end is None:
                raise ValueError("extracted events require char_start and char_end")
            if char_start < 0 or char_end <= char_start:
                raise ValueError("invalid source span")
            text = self.get_source_text(story_source_id)
            if char_end > len(text):
                raise ValueError("source span out of range")
            quote = text[char_start:char_end]
            quote_hash = content_hash(quote)
            chunk_id = self._locate_chunk(story_source_id, char_start, char_end)
        else:
            quote_hash = None
            chunk_id = None
            story_source_id = None
            char_start = None
            char_end = None

        branch_id = self.primary_branch_id()
        event_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        now = utc_now()
        stable_key = f"evt-{uuid.uuid4().hex[:12]}"
        self._db.connection.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                """
                INSERT INTO narrative_events(id, branch_id, stable_key, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, branch_id, stable_key, now),
            )
            self._db.execute(
                """
                INSERT INTO narrative_event_revisions(
                    id, event_id, branch_id, title, summary, order_key, story_time,
                    origin, confidence, status, story_source_id, source_chunk_id,
                    char_start, char_end, quote_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    event_id,
                    branch_id,
                    safe_title,
                    safe_summary,
                    float(order_key),
                    origin,
                    float(confidence),
                    story_source_id,
                    chunk_id,
                    char_start,
                    char_end,
                    quote_hash,
                    now,
                ),
            )
            self._db.connection.execute("COMMIT")
        except Exception:
            self._db.connection.execute("ROLLBACK")
            raise
        return NarrativeEventView(
            event_id=event_id,
            revision_id=revision_id,
            branch_id=branch_id,
            title=safe_title,
            summary=safe_summary,
            order_key=float(order_key),
            origin=origin,
            status="active",
            story_source_id=story_source_id,
            source_chunk_id=chunk_id,
            char_start=char_start,
            char_end=char_end,
            quote_hash=quote_hash,
            confidence=float(confidence),
        )

    def list_events(self) -> list[NarrativeEventView]:
        branch_id = self.primary_branch_id()
        rows = self._db.fetchall(
            """
            SELECT * FROM narrative_event_revisions
            WHERE branch_id = ? AND status = 'active'
            ORDER BY order_key ASC, created_at ASC
            """,
            (branch_id,),
        )
        return [
            NarrativeEventView(
                event_id=row["event_id"],
                revision_id=row["id"],
                branch_id=row["branch_id"],
                title=row["title"],
                summary=row["summary"],
                order_key=float(row["order_key"]),
                origin=row["origin"],
                status=row["status"],
                story_source_id=row["story_source_id"],
                source_chunk_id=row["source_chunk_id"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                quote_hash=row["quote_hash"],
                confidence=float(row["confidence"]),
            )
            for row in rows
        ]

    def create_edge(
        self,
        *,
        from_event_id: str,
        to_event_id: str,
        relation: str,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        if relation not in EDGE_RELATIONS:
            raise ValueError(f"invalid relation: {relation}")
        if from_event_id == to_event_id:
            raise ValueError("edge endpoints must differ")
        for event_id in (from_event_id, to_event_id):
            row = self._db.fetchone(
                "SELECT id FROM narrative_events WHERE id = ?", (event_id,)
            )
            if row is None:
                raise ValueError(f"event not found: {event_id}")
        branch_id = self.primary_branch_id()
        edge_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO narrative_event_edges(
                id, branch_id, from_event_id, to_event_id, relation, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                branch_id,
                from_event_id,
                to_event_id,
                relation,
                float(confidence),
                now,
            ),
        )
        self._db.commit()
        return {
            "id": edge_id,
            "branch_id": branch_id,
            "from_event_id": from_event_id,
            "to_event_id": to_event_id,
            "relation": relation,
            "confidence": float(confidence),
            "created_at": now,
        }

    def list_edges(self) -> list[dict[str, Any]]:
        branch_id = self.primary_branch_id()
        rows = self._db.fetchall(
            """
            SELECT * FROM narrative_event_edges
            WHERE branch_id = ?
            ORDER BY created_at ASC
            """,
            (branch_id,),
        )
        return [
            {
                "id": row["id"],
                "branch_id": row["branch_id"],
                "from_event_id": row["from_event_id"],
                "to_event_id": row["to_event_id"],
                "relation": row["relation"],
                "confidence": float(row["confidence"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _locate_chunk(
        self, source_id: str, char_start: int, char_end: int
    ) -> str | None:
        rows = self.list_chunks(source_id, latest_only=True)
        for chunk in rows:
            if chunk.char_start <= char_start and char_end <= chunk.char_end:
                return chunk.id
        return None


def _split_into_chunks(text: str) -> list[tuple[str | None, int, int]]:
    matches = list(CHAPTER_HEADING.finditer(text))
    if matches:
        chunks: list[tuple[str | None, int, int]] = []
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            title = match.group(0).strip().lstrip("#").strip()
            if end > start:
                chunks.append((title, start, end))
        if chunks:
            return chunks

    # Fallback: paragraph windows by blank lines.
    parts = re.split(r"\n\s*\n", text)
    chunks = []
    cursor = 0
    ordinal = 1
    for part in parts:
        if not part.strip():
            cursor = text.find(part, cursor) + len(part)
            continue
        start = text.find(part, cursor)
        end = start + len(part)
        chunks.append((f"片段 {ordinal}", start, end))
        cursor = end
        ordinal += 1
    if not chunks and text:
        return [("全文", 0, len(text))]
    return chunks


def _row_source(row: Any) -> StorySourceRecord:
    return StorySourceRecord(
        id=row["id"],
        source_type=row["source_type"],
        title=row["title"],
        status=row["status"],
        text_path=row["text_path"],
        content_hash=row["content_hash"],
        char_count=int(row["char_count"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_chunk(row: Any) -> SourceChunkRecord:
    return SourceChunkRecord(
        id=row["id"],
        story_source_id=row["story_source_id"],
        parent_chunk_id=row["parent_chunk_id"],
        chunk_type=row["chunk_type"],
        ordinal=int(row["ordinal"]),
        title=row["title"],
        char_start=int(row["char_start"]),
        char_end=int(row["char_end"]),
        content_hash=row["content_hash"],
        split_batch_id=row["split_batch_id"],
        created_at=row["created_at"],
    )
