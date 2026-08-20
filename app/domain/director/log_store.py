from __future__ import annotations

import sqlite3

from app.core.time import utc_now
from app.runtime.sqlite import (
    ensure_exact_schema_version,
    transaction,
)

SCHEMA_VERSION = 1
KEEP_TURNS = 3
MAX_RECENT_ENTRIES = 6
PHASE_ORDER = {"generation": 0, "confirmation": 1}


class RecentDirectorLogStore:
    """Bounded per-turn record of the two-phase Director decisions.

    Rows are keyed by ``(turn_index, phase)`` so rerolls and retries overwrite
    rather than accumulate. The store keeps only the most recent ``KEEP_TURNS``
    turns (at most ``MAX_RECENT_ENTRIES`` entries) and is written atomically
    with confirmation commit.
    """

    def __init__(self, conn: sqlite3.Connection, *, owns_connection: bool = False):
        self._conn = conn
        self._owns_connection = owns_connection
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS director_log (
                turn_index INTEGER NOT NULL,
                phase TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reasoning TEXT NOT NULL,
                measures TEXT NOT NULL,
                PRIMARY KEY (turn_index, phase)
            )
            """
        )
        ensure_exact_schema_version(self._conn, "director_log", SCHEMA_VERSION)
        self._conn.commit()

    def save_entries(
        self,
        entries: list[dict],
        *,
        keep_turns: int = KEEP_TURNS,
    ) -> None:
        if not entries:
            return
        with transaction(self._conn):
            for entry in entries:
                self._conn.execute(
                    """
                    INSERT INTO director_log (
                        turn_index, phase, created_at, reasoning, measures
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(turn_index, phase) DO UPDATE SET
                        created_at = excluded.created_at,
                        reasoning = excluded.reasoning,
                        measures = excluded.measures
                    """,
                    (
                        int(entry["turn_index"]),
                        str(entry["phase"]),
                        str(entry.get("created_at") or utc_now()),
                        str(entry.get("reasoning") or ""),
                        str(entry.get("measures") or ""),
                    ),
                )
        self._prune(keep_turns)

    def _prune(self, keep_turns: int) -> None:
        row = self._conn.execute(
            "SELECT MAX(turn_index) AS mx FROM director_log"
        ).fetchone()
        if row is None or row["mx"] is None:
            return
        cutoff = int(row["mx"]) - keep_turns
        with transaction(self._conn):
            self._conn.execute(
                "DELETE FROM director_log WHERE turn_index <= ?",
                (cutoff,),
            )

    def recent(self, *, limit: int = MAX_RECENT_ENTRIES) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT turn_index, phase, reasoning, measures
            FROM director_log
            ORDER BY turn_index DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "turn_index": int(row["turn_index"]),
                "phase": str(row["phase"]),
                "reasoning": str(row["reasoning"]),
                "measures": str(row["measures"]),
            }
            for row in rows
        ]

    def count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM director_log"
        ).fetchone()
        return int(row["count"])

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()

    def __enter__(self) -> "RecentDirectorLogStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()
