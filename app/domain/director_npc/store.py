import json
import sqlite3

from app.runtime.sqlite import connect_sqlite, ensure_exact_schema_version

from .schemas import DirectorNPCDraft, utc_now


class DirectorNPCStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect_sqlite(db_path)
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS director_npcs (
                draft_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                draft_json TEXT NOT NULL,
                created_turn INTEGER NOT NULL,
                introduced_turn INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        expected = {
            "draft_id",
            "status",
            "draft_json",
            "created_turn",
            "introduced_turn",
            "created_at",
            "updated_at",
        }
        actual = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(director_npcs)"
            ).fetchall()
        }
        if actual != expected:
            raise RuntimeError(
                "director_npcs.db does not match the current Director Agent contract"
            )
        ensure_exact_schema_version(self._conn, "director_npc", 3)
        self._conn.commit()

    def save(self, draft: DirectorNPCDraft) -> str:
        updated = draft.model_copy(update={"updated_at": utc_now()})
        self._conn.execute(
            """
            INSERT OR REPLACE INTO director_npcs (
                draft_id, status, draft_json, created_turn,
                introduced_turn, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                updated.draft_id,
                updated.status,
                updated.model_dump_json(),
                updated.created_turn,
                updated.introduced_turn,
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

    def list_prepared(self) -> list[DirectorNPCDraft]:
        rows = self._conn.execute(
            """
            SELECT draft_json FROM director_npcs
            WHERE status = 'prepared'
            ORDER BY created_turn, created_at
            """
        ).fetchall()
        return [self._draft_from_row(row) for row in rows]

    def list_all(self) -> list[DirectorNPCDraft]:
        rows = self._conn.execute(
            """
            SELECT draft_json FROM director_npcs
            ORDER BY created_turn, created_at
            """
        ).fetchall()
        return [self._draft_from_row(row) for row in rows]

    def mark_introduced(
        self,
        draft_id: str,
        *,
        turn_index: int,
    ) -> DirectorNPCDraft | None:
        draft = self.get_by_id(draft_id)
        if draft is None or draft.status == "retired":
            return None
        if draft.status == "introduced":
            return draft
        updated = draft.model_copy(
            update={
                "status": "introduced",
                "introduced_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return updated

    def retire(self, draft_id: str) -> DirectorNPCDraft | None:
        draft = self.get_by_id(draft_id)
        if draft is None:
            return None
        updated = draft.model_copy(
            update={"status": "retired", "updated_at": utc_now()}
        )
        self.save(updated)
        return updated

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @staticmethod
    def _draft_from_row(row: sqlite3.Row) -> DirectorNPCDraft:
        return DirectorNPCDraft.model_validate(json.loads(row["draft_json"]))
