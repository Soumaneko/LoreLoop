import json
import sqlite3

from app.core.time import utc_now
from app.domain.current_state.schemas import (
    CurrentState,
    PlayerState,
    ResourcesState,
    SceneState,
    WorldClock,
)
from app.runtime.sqlite import ensure_schema_version, transaction


def make_default_state(scene_id: str = "scene_001") -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id=scene_id,
            location="",
            participants=[],
        ),
        world_clock=WorldClock(day=1, phase="清晨"),
        player=PlayerState(
            name="",
            visible_status=None,
        ),
        resources=ResourcesState(
            inventory=[],
            money=None,
            fixed_assets=[],
            temporary_assets=[],
        ),
    )


class CurrentStateStore:
    def __init__(self, conn: sqlite3.Connection, *, owns_connection: bool = False):
        self._conn = conn
        self._owns_connection = owns_connection
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS current_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_schema_version(self._conn, "current_state", 4)
        self._migrate_removed_condition_fields()
        self._conn.commit()

    def _migrate_removed_condition_fields(self) -> None:
        row = self._conn.execute(
            "SELECT state_json FROM current_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return
        raw_json = str(row["state_json"])
        state = CurrentState.model_validate_json(raw_json)
        migrated_json = state.model_dump_json()
        if migrated_json == raw_json:
            return
        self._conn.execute(
            """
            UPDATE current_state
            SET state_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                migrated_json,
                utc_now(),
            ),
        )

    def save(self, state: CurrentState) -> None:
        now = utc_now()
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO current_state (id, state_json, updated_at)
                VALUES (1, ?, ?)
                """,
                (state.model_dump_json(), now),
            )

    def load(self) -> CurrentState | None:
        row = self._conn.execute(
            "SELECT state_json FROM current_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return CurrentState.model_validate_json(row["state_json"])

    def load_or_default(self, scene_id: str = "scene_001") -> CurrentState:
        state = self.load()
        if state is None:
            state = make_default_state(scene_id)
            self.save(state)
        return state

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()

    def __enter__(self) -> "CurrentStateStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()
