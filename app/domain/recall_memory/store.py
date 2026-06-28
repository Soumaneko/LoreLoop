import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.domain.recall_memory.schemas import (
    Memory,
    MemoryRecord,
    MemoryStatus,
    legacy_evidence_mode,
    legacy_memory_kind,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        self._create_table()
        self._migrate_legacy_schema()
        self._conn.execute("DROP INDEX IF EXISTS idx_memories_dedup")
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup_v2
            ON memories(
                memory_kind, evidence_mode, IFNULL(owner, ''),
                subject, entities_json, content
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_status_created
            ON memories(status, created_at)
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _create_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                memory_kind TEXT,
                evidence_mode TEXT,
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                entities_json TEXT NOT NULL DEFAULT '[]',
                location TEXT,
                time_label TEXT,
                visibility_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 1.0,
                salience REAL NOT NULL DEFAULT 0.5,
                owner TEXT,
                source_scene_id TEXT,
                source_text_span_json TEXT NOT NULL DEFAULT '[]',
                source_pending_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                status_reason TEXT,
                superseded_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                layer TEXT NOT NULL DEFAULT 'truth',
                subtype TEXT NOT NULL DEFAULT 'fact',
                active INTEGER NOT NULL DEFAULT 1,
                merged_into TEXT
            )
            """
        )

    def _migrate_legacy_schema(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        additions = {
            "memory_kind": "TEXT",
            "evidence_mode": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "status_reason": "TEXT",
            "superseded_by": "TEXT",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")

        rows = self._conn.execute(
            """
            SELECT id, layer, subtype, active, merged_into, created_at,
                   memory_kind, evidence_mode, status, superseded_by, updated_at
            FROM memories
            """
        ).fetchall()
        for row in rows:
            memory_kind = row["memory_kind"] or legacy_memory_kind(
                row["layer"], row["subtype"]
            )
            evidence_mode = row["evidence_mode"] or legacy_evidence_mode(row["layer"])
            status = row["status"] or (
                "active" if bool(row["active"]) else "superseded"
            )
            if not bool(row["active"]) and status == "active":
                status = "superseded"
            superseded_by = row["superseded_by"] or row["merged_into"]
            updated_at = row["updated_at"] or row["created_at"] or _utc_now()
            self._conn.execute(
                """
                UPDATE memories
                SET memory_kind = ?, evidence_mode = ?, status = ?,
                    superseded_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    memory_kind,
                    evidence_mode,
                    status,
                    superseded_by,
                    updated_at,
                    row["id"],
                ),
            )

    def _make_dedup_key(self, memory: Memory) -> tuple:
        return (
            memory.memory_kind,
            memory.evidence_mode,
            memory.owner or "",
            memory.subject,
            json.dumps(sorted(memory.entities), ensure_ascii=False),
            memory.content,
        )

    def _get_existing_id(self, memory: Memory) -> str | None:
        row = self._conn.execute(
            """
            SELECT id FROM memories
            WHERE memory_kind = ?
              AND evidence_mode = ?
              AND IFNULL(owner, '') = ?
              AND subject = ?
              AND entities_json = ?
              AND content = ?
            """,
            self._make_dedup_key(memory),
        ).fetchone()
        return str(row["id"]) if row is not None else None

    def save_many(self, memories: list[Memory]) -> list[str]:
        saved_ids: list[str] = []
        now = _utc_now()
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
                id, memory_kind, evidence_mode, subject, content,
                entities_json, location, time_label,
                visibility_json, tags_json,
                confidence, salience, owner,
                source_scene_id, source_text_span_json, source_pending_ids_json,
                status, status_reason, superseded_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                memory.memory_kind,
                memory.evidence_mode,
                memory.subject,
                memory.content,
                json.dumps(sorted(memory.entities), ensure_ascii=False),
                memory.location,
                memory.time_label,
                json.dumps(memory.visibility, ensure_ascii=False),
                json.dumps(memory.tags, ensure_ascii=False),
                memory.confidence,
                memory.salience,
                memory.owner,
                memory.source_scene_id,
                json.dumps(memory.source_text_span, ensure_ascii=False),
                json.dumps(memory.source_pending_ids, ensure_ascii=False),
                "active",
                None,
                None,
                now,
                now,
            ),
        )
        return memory_id if cursor.rowcount > 0 else None

    def save_one_returning_id(self, memory: Memory) -> str:
        existing_id = self._get_existing_id(memory)
        if existing_id is not None:
            return existing_id
        now = _utc_now()
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
            """
            SELECT * FROM memories
            WHERE status = 'active'
            ORDER BY created_at DESC
            """
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_by_id(self, memory_id: str) -> MemoryRecord | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            memory_kind=row["memory_kind"],
            evidence_mode=row["evidence_mode"],
            subject=row["subject"],
            content=row["content"],
            entities=json.loads(row["entities_json"]),
            location=row["location"],
            time_label=row["time_label"],
            visibility=json.loads(row["visibility_json"]),
            tags=json.loads(row["tags_json"]),
            confidence=row["confidence"],
            salience=row["salience"],
            owner=row["owner"],
            source_scene_id=row["source_scene_id"],
            source_text_span=json.loads(row["source_text_span_json"]),
            source_pending_ids=json.loads(row["source_pending_ids_json"]),
            status=row["status"],
            status_reason=row["status_reason"],
            superseded_by=row["superseded_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    def set_status(
        self,
        memory_ids: list[str],
        *,
        status: MemoryStatus,
        reason: str,
        superseded_by: str | None = None,
    ) -> None:
        if not memory_ids:
            return
        now = _utc_now()
        self._conn.executemany(
            """
            UPDATE memories
            SET status = ?, status_reason = ?, superseded_by = ?,
                updated_at = ?, active = ?, merged_into = ?
            WHERE id = ? AND status = 'active'
            """,
            [
                (
                    status,
                    reason,
                    superseded_by,
                    now,
                    int(status == "active"),
                    superseded_by,
                    memory_id,
                )
                for memory_id in memory_ids
            ],
        )
        self._conn.commit()

    def get_meta_int(self, key: str, default: int = 0) -> int:
        row = self._conn.execute(
            "SELECT value FROM memory_meta WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return default

    def set_meta_int(self, key: str, value: int) -> None:
        self._conn.execute(
            """
            INSERT INTO memory_meta(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
