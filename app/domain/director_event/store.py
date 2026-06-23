import json
import sqlite3

from .schemas import DirectorEvent, DirectorEventExposurePhase, utc_now


class DirectorEventStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS director_events (
                event_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_turn INTEGER NOT NULL,
                updated_turn INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def save(self, event: DirectorEvent) -> str:
        if event.status == "active":
            self._suspend_other_active(event.event_id, event.updated_turn or event.created_turn)
        now = utc_now()
        event = event.model_copy(update={"updated_at": now})
        self._conn.execute(
            """
            INSERT OR REPLACE INTO director_events (
                event_id, status, event_json, created_turn, updated_turn, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.status,
                event.model_dump_json(),
                event.created_turn,
                event.updated_turn,
                event.created_at,
                event.updated_at,
            ),
        )
        self._conn.commit()
        return event.event_id

    def get_active(self) -> DirectorEvent | None:
        row = self._conn.execute(
            "SELECT event_json FROM director_events WHERE status = 'active' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return self._event_from_row(row) if row else None

    def list_suspended(self) -> list[DirectorEvent]:
        return self._list_by_status("suspended")

    def list_archived(self) -> list[DirectorEvent]:
        return self._list_by_status("archived")

    def list_all(self) -> list[DirectorEvent]:
        rows = self._conn.execute(
            "SELECT event_json FROM director_events ORDER BY created_turn ASC, created_at ASC"
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def activate(self, event_id: str, *, turn_index: int) -> bool:
        event = self.get_by_id(event_id)
        if event is None or event.status == "archived":
            return False
        self._suspend_other_active(event_id, turn_index)
        updated = event.model_copy(
            update={
                "status": "active",
                "suspended_reason": None,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def suspend(self, event_id: str, reason: str, *, turn_index: int) -> bool:
        event = self.get_by_id(event_id)
        if event is None or event.status != "active":
            return False
        updated = event.model_copy(
            update={
                "status": "suspended",
                "exposure_phase": "hidden",
                "suspended_reason": reason,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def archive_active(self, event_id: str, *, turn_index: int) -> bool:
        event = self.get_by_id(event_id)
        if event is None or event.status != "active":
            return False
        updated = event.model_copy(
            update={
                "status": "archived",
                "suspended_reason": None,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def update_dynamic_fields(
        self,
        event_id: str,
        *,
        exposure_phase_update: DirectorEventExposurePhase | None = None,
        known_to_player_updates: list[str] | None = None,
        hidden_development_updates: list[str] | None = None,
        turn_index: int,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None:
            return False

        known_to_player = _merge_unique(
            event.known_to_player,
            known_to_player_updates,
        )
        hidden_developments = _merge_unique(
            event.hidden_developments,
            hidden_development_updates,
        )
        exposure_phase = event.exposure_phase
        if exposure_phase_update == "engaged":
            exposure_phase = "engaged"

        updated = event.model_copy(
            update={
                "exposure_phase": exposure_phase,
                "known_to_player": known_to_player,
                "hidden_developments": hidden_developments,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def touch(self, event_id: str, *, turn_index: int) -> bool:
        event = self.get_by_id(event_id)
        if event is None:
            return False
        updated = event.model_copy(update={"updated_turn": turn_index, "updated_at": utc_now()})
        self.save(updated)
        return True

    def get_by_id(self, event_id: str) -> DirectorEvent | None:
        row = self._conn.execute(
            "SELECT event_json FROM director_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return self._event_from_row(row) if row else None

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM director_events").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _list_by_status(self, status: str) -> list[DirectorEvent]:
        rows = self._conn.execute(
            "SELECT event_json FROM director_events WHERE status = ? ORDER BY updated_at DESC",
            (status,),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def _suspend_other_active(self, event_id: str, turn_index: int) -> None:
        active = self.get_active()
        if active is None or active.event_id == event_id:
            return
        updated = active.model_copy(
            update={
                "status": "suspended",
                "exposure_phase": "hidden",
                "suspended_reason": "被新的导演事件替换",
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self._write_without_active_guard(updated)

    def _write_without_active_guard(self, event: DirectorEvent) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO director_events (
                event_id, status, event_json, created_turn, updated_turn, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.status,
                event.model_dump_json(),
                event.created_turn,
                event.updated_turn,
                event.created_at,
                event.updated_at,
            ),
        )
        self._conn.commit()

    @staticmethod
    def _event_from_row(row) -> DirectorEvent:
        data = json.loads(row["event_json"])
        return DirectorEvent.model_validate(data)


def _merge_unique(existing: list[str] | None, updates: list[str] | None) -> list[str] | None:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*(existing or []), *(updates or [])]:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        merged.append(cleaned)
        seen.add(cleaned)
    return merged or None
