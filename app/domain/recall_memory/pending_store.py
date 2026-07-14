import json
import sqlite3
from app.runtime.sqlite import connect_sqlite, ensure_schema_version, transaction

from app.domain.recall_memory.schemas import PendingMemoryItem


class PendingMemoryStore:
    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn = connect_sqlite(db_path)
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_memory_items (
                id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                memory_kind TEXT,
                subject TEXT,
                raw_text TEXT NOT NULL,
                source_text_span TEXT NOT NULL,
                entities_json TEXT NOT NULL DEFAULT '[]',
                owner TEXT,
                location TEXT,
                time_label TEXT,
                importance_hint TEXT NOT NULL DEFAULT 'medium',
                created_at TEXT NOT NULL,
                classified INTEGER NOT NULL DEFAULT 0,
                discarded INTEGER NOT NULL DEFAULT 0,
                discard_reason TEXT
            )
            """
        )
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(pending_memory_items)"
            ).fetchall()
        }
        for name, ddl in {"memory_kind": "TEXT", "subject": "TEXT", "owner": "TEXT"}.items():
            if name not in columns:
                self._conn.execute(
                    f"ALTER TABLE pending_memory_items ADD COLUMN {name} {ddl}"
                )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pending_memory_unclassified
            ON pending_memory_items(classified, discarded, turn_index, created_at)
            """
        )
        ensure_schema_version(self._conn, "pending_memory", 1)
        self._conn.commit()

    def save_many(self, items: list[PendingMemoryItem]) -> list[str]:
        saved_ids: list[str] = []
        with transaction(self._conn):
            for item in items:
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO pending_memory_items (
                        id, scene_id, turn_index, memory_kind, subject,
                        raw_text, source_text_span, entities_json, owner,
                        location, time_label, importance_hint, created_at,
                        classified, discarded, discard_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id,
                        item.scene_id,
                        item.turn_index,
                        item.memory_kind,
                        item.subject,
                        item.raw_text,
                        json.dumps(item.source_text_span, ensure_ascii=False),
                        json.dumps(item.entities, ensure_ascii=False),
                        item.owner,
                        item.location,
                        item.time_label,
                        item.importance_hint,
                        item.created_at,
                        int(item.classified),
                        int(item.discarded),
                        item.discard_reason,
                    ),
                )
                if cursor.rowcount > 0:
                    saved_ids.append(item.id)
        return saved_ids

    def list_unclassified(self, *, limit: int | None = None) -> list[PendingMemoryItem]:
        sql = (
            "SELECT * FROM pending_memory_items "
            "WHERE classified = 0 AND discarded = 0 "
            "ORDER BY turn_index ASC, created_at ASC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def list_recent_unclassified(self, *, limit: int = 20) -> list[PendingMemoryItem]:
        rows = self._conn.execute(
            """
            SELECT * FROM pending_memory_items
            WHERE classified = 0 AND discarded = 0
            ORDER BY turn_index DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def mark_classified(self, ids: list[str]) -> None:
        if not ids:
            return
        self._conn.executemany(
            "UPDATE pending_memory_items SET classified = 1 WHERE id = ?",
            [(item_id,) for item_id in ids],
        )
        self._conn.commit()

    def mark_discarded(self, ids: list[str], reason: str | None = None) -> None:
        if not ids:
            return
        self._conn.executemany(
            """
            UPDATE pending_memory_items
            SET classified = 1, discarded = 1, discard_reason = ?
            WHERE id = ?
            """,
            [(reason, item_id) for item_id in ids],
        )
        self._conn.commit()

    def unclassified_turn_count(self) -> int:
        row = self._conn.execute(
            """
            SELECT COUNT(DISTINCT turn_index)
            FROM pending_memory_items
            WHERE classified = 0 AND discarded = 0
            """
        ).fetchone()
        return int(row[0])

    def should_trigger_batch(self, *, turn_threshold: int = 5) -> bool:
        return self.unclassified_turn_count() >= turn_threshold

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM pending_memory_items").fetchone()
        return int(row[0])

    def count_unclassified(self) -> int:
        row = self._conn.execute(
            """
            SELECT COUNT(*)
            FROM pending_memory_items
            WHERE classified = 0 AND discarded = 0
            """
        ).fetchone()
        return int(row[0])

    def _row_to_item(self, row: sqlite3.Row) -> PendingMemoryItem:
        return PendingMemoryItem(
            id=row["id"],
            scene_id=row["scene_id"],
            turn_index=row["turn_index"],
            memory_kind=row["memory_kind"],
            subject=row["subject"] or row["raw_text"][:80],
            raw_text=row["raw_text"],
            source_text_span=_load_source_text_span(row["source_text_span"]),
            entities=json.loads(row["entities_json"]),
            owner=row["owner"],
            location=row["location"],
            time_label=row["time_label"],
            importance_hint=row["importance_hint"],
            created_at=row["created_at"],
            classified=bool(row["classified"]),
            discarded=bool(row["discarded"]),
            discard_reason=row["discard_reason"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _load_source_text_span(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("source_text_span must be stored as a JSON list")
    return [str(item) for item in parsed if str(item)]
