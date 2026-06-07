import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.domain.memory.schemas import Memory, MemoryRecord


class MemoryStore:
    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                entities_json TEXT NOT NULL DEFAULT '[]',
                location TEXT,
                time_label TEXT,
                visibility_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                certainty REAL NOT NULL DEFAULT 1.0,
                salience REAL NOT NULL DEFAULT 0.5,
                source_scene_id TEXT,
                source_text_span_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup
            ON memories(type, subject, entities_json, content)
            """
        )
        self._conn.commit()

    def _make_dedup_key(self, memory: Memory) -> tuple:
        return (
            memory.type,
            memory.subject,
            json.dumps(sorted(memory.entities), ensure_ascii=False),
            memory.content,
        )

    def _exists(self, memory: Memory) -> bool:
        row = self._conn.execute(
            """
            SELECT id FROM memories
            WHERE type = ? AND subject = ? AND entities_json = ? AND content = ?
            """,
            self._make_dedup_key(memory),
        ).fetchone()
        return row is not None

    def save_many(self, memories: list[Memory]) -> list[str]:
        saved_ids: list[str] = []
        now = datetime.now(timezone.utc).isoformat()

        for memory in memories:
            memory_id = uuid.uuid4().hex
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO memories (
                    id, type, subject, content,
                    entities_json, location, time_label,
                    visibility_json, tags_json,
                    certainty, salience,
                    source_scene_id, source_text_span_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    memory.type,
                    memory.subject,
                    memory.content,
                    json.dumps(memory.entities, ensure_ascii=False),
                    memory.location,
                    memory.time_label,
                    json.dumps(memory.visibility, ensure_ascii=False),
                    json.dumps(memory.tags, ensure_ascii=False),
                    memory.certainty,
                    memory.salience,
                    memory.source_scene_id,
                    json.dumps(memory.source_text_span, ensure_ascii=False),
                    now,
                ),
            )
            if cursor.rowcount > 0:
                saved_ids.append(memory_id)

        self._conn.commit()
        return saved_ids

    def list_all(self) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_by_id(self, memory_id: str) -> MemoryRecord | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            type=row["type"],
            subject=row["subject"],
            content=row["content"],
            entities=json.loads(row["entities_json"]),
            location=row["location"],
            time_label=row["time_label"],
            visibility=json.loads(row["visibility_json"]),
            tags=json.loads(row["tags_json"]),
            certainty=row["certainty"],
            salience=row["salience"],
            source_scene_id=row["source_scene_id"],
            source_text_span=json.loads(row["source_text_span_json"]),
            created_at=row["created_at"],
        )

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
