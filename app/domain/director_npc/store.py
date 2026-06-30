import json
import sqlite3

from .schemas import DirectorNPCDraft, utc_now


class DirectorNPCStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS director_npcs (
                draft_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                draft_json TEXT NOT NULL,
                created_turn INTEGER NOT NULL,
                dispatched_turn INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS director_npc_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        self._conn.commit()

    def save(self, draft: DirectorNPCDraft) -> str:
        updated = draft.model_copy(update={"updated_at": utc_now()})
        self._conn.execute(
            """
            INSERT OR REPLACE INTO director_npcs (
                draft_id, status, draft_json, created_turn,
                dispatched_turn, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                updated.draft_id,
                updated.status,
                updated.model_dump_json(),
                updated.created_turn,
                updated.dispatched_turn,
                updated.created_at,
                updated.updated_at,
            ),
        )
        self._conn.commit()
        return updated.draft_id

    def get_by_id(self, draft_id: str) -> DirectorNPCDraft | None:
        row = self._conn.execute(
            "SELECT draft_json FROM director_npcs WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        return self._draft_from_row(row) if row else None

    def list_standby(self) -> list[DirectorNPCDraft]:
        rows = self._conn.execute(
            """
            SELECT draft_json FROM director_npcs
            WHERE status = 'standby'
            ORDER BY created_turn ASC, created_at ASC
            """
        ).fetchall()
        return [self._draft_from_row(row) for row in rows]

    def list_all(self) -> list[DirectorNPCDraft]:
        rows = self._conn.execute(
            "SELECT draft_json FROM director_npcs ORDER BY created_turn ASC, created_at ASC"
        ).fetchall()
        return [self._draft_from_row(row) for row in rows]

    def mark_dispatched(self, draft_id: str, *, turn_index: int) -> DirectorNPCDraft | None:
        draft = self.get_by_id(draft_id)
        if draft is None or draft.status != "standby":
            return None
        updated = draft.model_copy(
            update={
                "status": "dispatched",
                "dispatched_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        self.set_meta_int("last_dispatch_turn", turn_index)
        return updated

    def get_meta_int(self, key: str, default: int = 0) -> int:
        row = self._conn.execute(
            "SELECT value FROM director_npc_meta WHERE key = ?",
            (key,),
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
            INSERT INTO director_npc_meta(key, value)
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

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @staticmethod
    def _draft_from_row(row: sqlite3.Row) -> DirectorNPCDraft:
        return DirectorNPCDraft.model_validate(json.loads(row["draft_json"]))
