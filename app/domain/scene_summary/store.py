import json
import sqlite3

from app.domain.scene_summary.schemas import SceneSummary


class SceneSummaryStore:
    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scene_summaries (
                id TEXT PRIMARY KEY,
                level INTEGER NOT NULL,
                content TEXT NOT NULL,
                scene_ids_json TEXT NOT NULL DEFAULT '[]',
                source_ids_json TEXT NOT NULL DEFAULT '[]',
                turn_start INTEGER NOT NULL,
                turn_end INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                condensed_into TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scene_summaries_active_level
            ON scene_summaries(active, level, turn_start, turn_end, created_at)
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scene_summary_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def save_one(self, summary: SceneSummary) -> str:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO scene_summaries (
                id, level, content, scene_ids_json, source_ids_json,
                turn_start, turn_end, active, condensed_into, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.id,
                summary.level,
                summary.content,
                json.dumps(summary.scene_ids, ensure_ascii=False),
                json.dumps(summary.source_ids, ensure_ascii=False),
                summary.turn_start,
                summary.turn_end,
                int(summary.active),
                summary.condensed_into,
                summary.created_at,
            ),
        )
        self._conn.commit()
        return summary.id

    def list_uncondensed(
        self,
        level: int,
        *,
        limit: int | None = None,
    ) -> list[SceneSummary]:
        sql = (
            "SELECT * FROM scene_summaries "
            "WHERE active = 1 AND level = ? "
            "ORDER BY turn_start ASC, turn_end ASC, created_at ASC"
        )
        params: tuple = (level,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (level, limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def list_active(self) -> list[SceneSummary]:
        rows = self._conn.execute(
            """
            SELECT * FROM scene_summaries
            WHERE active = 1
            ORDER BY level DESC, turn_start ASC, turn_end ASC, created_at ASC
            """
        ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def get_recent(self, limit: int = 5) -> list[SceneSummary]:
        """Return recent active summaries, preferring lower levels at the same turn."""
        active = self.list_active()
        active.sort(key=lambda summary: (-summary.turn_end, summary.level))
        return active[:limit]

    def mark_condensed(self, ids: list[str], *, condensed_into: str) -> int:
        if not ids:
            return 0
        cursor = self._conn.executemany(
            """
            UPDATE scene_summaries
            SET active = 0, condensed_into = ?
            WHERE id = ? AND active = 1
            """,
            [(condensed_into, summary_id) for summary_id in ids],
        )
        self._conn.commit()
        return cursor.rowcount

    def should_condense(self, level: int, *, threshold: int = 10) -> bool:
        if threshold < 2:
            raise ValueError("threshold must be >= 2")
        return self.count_uncondensed(level) >= threshold

    def count_uncondensed(self, level: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM scene_summaries WHERE active = 1 AND level = ?",
            (level,),
        ).fetchone()
        return int(row[0])

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM scene_summaries").fetchone()
        return int(row[0])

    def _row_to_summary(self, row: sqlite3.Row) -> SceneSummary:
        return SceneSummary(
            id=row["id"],
            level=row["level"],
            content=row["content"],
            scene_ids=json.loads(row["scene_ids_json"]),
            source_ids=json.loads(row["source_ids_json"]),
            turn_start=row["turn_start"],
            turn_end=row["turn_end"],
            active=bool(row["active"]),
            condensed_into=row["condensed_into"],
            created_at=row["created_at"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
