import json
import sqlite3

from app.domain.recall_memory.schemas import PendingMemoryItem


class PendingMemoryStore:
    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_memory_items (
                id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                source_text_span TEXT NOT NULL,
                entities_json TEXT NOT NULL DEFAULT '[]',
                location TEXT,
                time_label TEXT,
                extractor_hint TEXT NOT NULL DEFAULT 'unknown',
                importance_hint TEXT NOT NULL DEFAULT 'medium',
                created_at TEXT NOT NULL,
                classified INTEGER NOT NULL DEFAULT 0,
                discarded INTEGER NOT NULL DEFAULT 0,
                discard_reason TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pending_memory_unclassified
            ON pending_memory_items(classified, discarded, turn_index, created_at)
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def save_many(self, items: list[PendingMemoryItem]) -> list[str]:
        saved_ids: list[str] = []
        for item in items:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO pending_memory_items (
                    id, scene_id, turn_index, raw_text, source_text_span,
                    entities_json, location, time_label, extractor_hint,
                    importance_hint, created_at, classified, discarded, discard_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.scene_id,
                    item.turn_index,
                    item.raw_text,
                    json.dumps(item.source_text_span, ensure_ascii=False),
                    json.dumps(item.entities, ensure_ascii=False),
                    item.location,
                    item.time_label,
                    item.extractor_hint,
                    item.importance_hint,
                    item.created_at,
                    int(item.classified),
                    int(item.discarded),
                    item.discard_reason,
                ),
            )
            if cursor.rowcount > 0:
                saved_ids.append(item.id)
        self._conn.commit()
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

    def batch_classify_count(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM pending_memory_meta WHERE key = ?",
            ("batch_classify_count",),
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    def record_batch_classified(self) -> int:
        count = self.batch_classify_count() + 1
        self._conn.execute(
            """
            INSERT INTO pending_memory_meta(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("batch_classify_count", str(count)),
        )
        self._conn.commit()
        return count

    def should_trigger_consolidation(self, *, every: int = 3) -> bool:
        if every < 1:
            raise ValueError("every must be >= 1")
        count = self.batch_classify_count()
        return count > 0 and count % every == 0

    def reset_batch_classify_count(self) -> None:
        self._conn.execute(
            "DELETE FROM pending_memory_meta WHERE key = ?",
            ("batch_classify_count",),
        )
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM pending_memory_items").fetchone()
        return int(row[0])

    def _row_to_item(self, row: sqlite3.Row) -> PendingMemoryItem:
        return PendingMemoryItem(
            id=row["id"],
            scene_id=row["scene_id"],
            turn_index=row["turn_index"],
            raw_text=row["raw_text"],
            source_text_span=_load_source_text_span(row["source_text_span"]),
            entities=json.loads(row["entities_json"]),
            location=row["location"],
            time_label=row["time_label"],
            extractor_hint=row["extractor_hint"],
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
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item)]
    if isinstance(parsed, str):
        return [parsed] if parsed else []
    return []
