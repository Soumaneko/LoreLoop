import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.domain.recall_memory.schemas import Memory, MemoryRecord


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
                layer TEXT NOT NULL DEFAULT 'truth',
                subtype TEXT NOT NULL DEFAULT 'fact',
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                entities_json TEXT NOT NULL DEFAULT '[]',
                location TEXT,
                time_label TEXT,
                visibility_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 1.0,
                certainty REAL NOT NULL DEFAULT 1.0,
                salience REAL NOT NULL DEFAULT 0.5,
                owner TEXT,
                source_scene_id TEXT,
                source_text_span_json TEXT NOT NULL DEFAULT '[]',
                source_pending_ids_json TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1,
                merged_into TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self._migrate_table()
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup
            ON memories(type, subject, entities_json, content)
            """
        )
        self._conn.commit()

    def _migrate_table(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        migrations = {
            "layer": "ALTER TABLE memories ADD COLUMN layer TEXT NOT NULL DEFAULT 'truth'",
            "subtype": "ALTER TABLE memories ADD COLUMN subtype TEXT NOT NULL DEFAULT 'fact'",
            "confidence": "ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0",
            "owner": "ALTER TABLE memories ADD COLUMN owner TEXT",
            "source_pending_ids_json": "ALTER TABLE memories ADD COLUMN source_pending_ids_json TEXT NOT NULL DEFAULT '[]'",
            "active": "ALTER TABLE memories ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
            "merged_into": "ALTER TABLE memories ADD COLUMN merged_into TEXT",
        }
        for column, statement in migrations.items():
            if column not in existing:
                self._conn.execute(statement)

    def _make_dedup_key(self, memory: Memory) -> tuple:
        return (
            memory.type,
            memory.subject,
            json.dumps(sorted(memory.entities), ensure_ascii=False),
            memory.content,
        )

    def _exists(self, memory: Memory) -> bool:
        return self._get_existing_id(memory) is not None

    def _get_existing_id(self, memory: Memory) -> str | None:
        row = self._conn.execute(
            """
            SELECT id FROM memories
            WHERE type = ? AND subject = ? AND entities_json = ? AND content = ?
            """,
            self._make_dedup_key(memory),
        ).fetchone()
        return str(row["id"]) if row is not None else None

    def save_many(self, memories: list[Memory]) -> list[str]:
        saved_ids: list[str] = []
        now = datetime.now(timezone.utc).isoformat()

        for memory in memories:
            memory_id = self._insert_memory(memory, now=now)
            if memory_id is not None:
                saved_ids.append(memory_id)

        self._conn.commit()
        return saved_ids

    def _insert_memory(self, memory: Memory, *, now: str) -> str | None:
        memory_id = uuid.uuid4().hex
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO memories (
                id, type, layer, subtype, subject, content,
                entities_json, location, time_label,
                visibility_json, tags_json,
                confidence, certainty, salience, owner,
                source_scene_id, source_text_span_json, source_pending_ids_json,
                active, merged_into, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                memory.type,
                memory.layer,
                memory.subtype,
                memory.subject,
                memory.content,
                json.dumps(sorted(memory.entities), ensure_ascii=False),
                memory.location,
                memory.time_label,
                json.dumps(memory.visibility, ensure_ascii=False),
                json.dumps(memory.tags, ensure_ascii=False),
                memory.confidence,
                memory.certainty,
                memory.salience,
                memory.owner,
                memory.source_scene_id,
                json.dumps(memory.source_text_span, ensure_ascii=False),
                json.dumps(memory.source_pending_ids, ensure_ascii=False),
                1,
                None,
                now,
            ),
        )
        return memory_id if cursor.rowcount > 0 else None

    def save_one_returning_id(self, memory: Memory) -> str:
        existing_id = self._get_existing_id(memory)
        if existing_id is not None:
            self._conn.execute(
                "UPDATE memories SET active = 1, merged_into = NULL WHERE id = ?",
                (existing_id,),
            )
            self._conn.commit()
            return existing_id
        now = datetime.now(timezone.utc).isoformat()
        memory_id = self._insert_memory(memory, now=now)
        self._conn.commit()
        if memory_id is None:
            existing_id = self._get_existing_id(memory)
            if existing_id is not None:
                return existing_id
            raise RuntimeError("memory insert failed")
        return memory_id

    def list_all(self) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_active(self) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE active = 1 ORDER BY created_at DESC"
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
            layer=row["layer"],
            subtype=row["subtype"],
            subject=row["subject"],
            content=row["content"],
            entities=json.loads(row["entities_json"]),
            location=row["location"],
            time_label=row["time_label"],
            visibility=json.loads(row["visibility_json"]),
            tags=json.loads(row["tags_json"]),
            confidence=row["confidence"],
            certainty=row["certainty"],
            salience=row["salience"],
            owner=row["owner"],
            source_scene_id=row["source_scene_id"],
            source_text_span=json.loads(row["source_text_span_json"]),
            source_pending_ids=json.loads(row["source_pending_ids_json"]),
            created_at=row["created_at"],
            active=bool(row["active"]),
            merged_into=row["merged_into"],
        )

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0]

    def deactivate(self, memory_id: str, *, merged_into: str) -> None:
        self._conn.execute(
            "UPDATE memories SET active = 0, merged_into = ? WHERE id = ?",
            (merged_into, memory_id),
        )
        self._conn.commit()

    def deactivate_many(self, memory_ids: list[str], *, merged_into: str) -> None:
        if not memory_ids:
            return
        self._conn.executemany(
            "UPDATE memories SET active = 0, merged_into = ? WHERE id = ?",
            [(merged_into, memory_id) for memory_id in memory_ids],
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
