from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from manus_mini.models import MemoryItem

SENSITIVE_PATTERN = re.compile(r"(API_KEY|TOKEN=|PASSWORD=|SECRET)", re.IGNORECASE)


class MemoryManager:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_message_ids TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at)"
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "MemoryManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def add(
        self,
        scope: str,
        kind: str,
        content: str,
        tags: Iterable[str],
        confidence: float = 1.0,
        source_message_ids: Iterable[str] | None = None,
    ) -> MemoryItem:
        now = datetime.now(UTC)
        item = MemoryItem(
            scope=scope,
            kind=kind,
            content=content,
            tags=list(tags),
            confidence=confidence,
            source_message_ids=list(source_message_ids or []),
            created_at=now,
            updated_at=now,
        )
        self._connection.execute(
            """
            INSERT INTO memories (
                id, scope, kind, content, tags, confidence,
                source_message_ids, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.scope,
                item.kind,
                item.content,
                json.dumps(item.tags, ensure_ascii=False),
                item.confidence,
                json.dumps(item.source_message_ids, ensure_ascii=False),
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
            ),
        )
        self._connection.commit()
        return item

    def add_if_allowed(
        self,
        scope: str,
        kind: str,
        content: str,
        tags: Iterable[str],
        confidence: float = 1.0,
        source_message_ids: Iterable[str] | None = None,
    ) -> MemoryItem | None:
        if SENSITIVE_PATTERN.search(content):
            return None
        return self.add(
            scope=scope,
            kind=kind,
            content=content,
            tags=tags,
            confidence=confidence,
            source_message_ids=source_message_ids,
        )

    def search(self, query: str, limit: int = 5) -> list[MemoryItem]:
        rows = self._connection.execute(
            """
            SELECT id, scope, kind, content, tags, confidence,
                   source_message_ids, created_at, updated_at
            FROM memories
            """
        ).fetchall()
        if not rows:
            return []

        tokens = [token for token in self._normalize_query(query) if token]
        scored: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            score = self._score_row(row, tokens, query)
            if score > 0 or not query.strip():
                scored.append((score, row))

        scored.sort(key=lambda item: (-item[0], item[1]["updated_at"], item[1]["id"]))
        return [self._row_to_item(row) for _, row in scored[:limit]]

    def _score_row(self, row: sqlite3.Row, tokens: list[str], query: str) -> int:
        if not query.strip():
            return 0

        content = row["content"].lower()
        tags = self._decode_json_list(row["tags"])
        tags_blob = " ".join(tag.lower() for tag in tags)
        query_blob = query.lower()

        score = 0
        if query_blob in content:
            score += 5
        if query_blob in tags_blob:
            score += 4

        for token in tokens:
            if token in content:
                score += 2
            if token in tags_blob:
                score += 3

        return score

    def _row_to_item(self, row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(
            id=row["id"],
            scope=row["scope"],
            kind=row["kind"],
            content=row["content"],
            tags=self._decode_json_list(row["tags"]),
            confidence=row["confidence"],
            source_message_ids=self._decode_json_list(row["source_message_ids"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _decode_json_list(raw: str) -> list[str]:
        value = json.loads(raw)
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    @staticmethod
    def _normalize_query(query: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", query.lower())


__all__ = ["MemoryManager"]
