import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ShortTermScene(BaseModel):
    scene_id: str
    turn_index: int
    user_input: str = ""
    scene_text: str
    narrative_type: str | None = None
    created_at: str | None = None


class ShortTermMemory(BaseModel):
    recent_scenes: list[ShortTermScene] = Field(default_factory=list)
    max_items: int = Field(default=5, ge=1)


class ShortTermMemoryStore:
    def __init__(self, db_path: str = ":memory:", max_items: int = 5):
        if max_items < 1:
            raise ValueError("max_items must be >= 1")
        self.max_items = max_items
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS short_term_scenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scene_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                user_input TEXT NOT NULL DEFAULT '',
                scene_text TEXT NOT NULL,
                narrative_type TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(short_term_scenes)").fetchall()
        }
        if "narrative_type" not in columns:
            self._conn.execute(
                "ALTER TABLE short_term_scenes ADD COLUMN narrative_type TEXT"
            )
        self._conn.commit()

    def add_turn(
        self,
        scene_id: str,
        turn_index: int,
        user_input: str,
        scene_text: str,
        narrative_type: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO short_term_scenes (
                scene_id, turn_index, user_input, scene_text, narrative_type, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (scene_id, turn_index, user_input, scene_text, narrative_type, now),
        )
        self._conn.commit()

    def get_recent(self) -> ShortTermMemory:
        rows = self._conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM short_term_scenes ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (self.max_items,),
        ).fetchall()
        return ShortTermMemory(
            recent_scenes=[
                self._row_to_scene(row) for row in rows
            ],
            max_items=self.max_items,
        )

    def list_all(self) -> list[ShortTermScene]:
        rows = self._conn.execute(
            "SELECT * FROM short_term_scenes ORDER BY id ASC"
        ).fetchall()
        return [self._row_to_scene(row) for row in rows]

    @staticmethod
    def _row_to_scene(row: sqlite3.Row) -> ShortTermScene:
        return ShortTermScene(
            scene_id=row["scene_id"],
            turn_index=row["turn_index"],
            user_input=row["user_input"],
            scene_text=row["scene_text"],
            narrative_type=row["narrative_type"],
            created_at=row["created_at"],
        )

    def clear(self) -> None:
        self._conn.execute("DELETE FROM short_term_scenes")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
