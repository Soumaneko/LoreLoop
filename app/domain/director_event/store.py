import json
import sqlite3

from pydantic import ValidationError

from app.domain.current_state.schemas import WorldClock

from .schemas import DirectorEvent, utc_now


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
            self._resolve_other_active(event.event_id, event.updated_turn or event.created_turn)
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

    def list_resolved(self) -> list[DirectorEvent]:
        return self._list_by_statuses(["resolved", "archived", "suspended"])

    def get_latest_inactive(self) -> DirectorEvent | None:
        return self.get_latest_resolved()

    def get_latest_resolved(self) -> DirectorEvent | None:
        row = self._conn.execute(
            """
            SELECT event_json FROM director_events
            WHERE status IN ('resolved', 'archived', 'suspended')
            ORDER BY COALESCE(updated_turn, created_turn) DESC, updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        return self._event_from_row(row) if row else None

    def get_recent_resolved(
        self,
        *,
        turn_index: int,
        cooldown_turns: int,
    ) -> DirectorEvent | None:
        event = self.get_latest_resolved()
        if event is None:
            return None
        resolved_turn = event.resolved_turn or event.updated_turn
        if resolved_turn is None or turn_index > resolved_turn + cooldown_turns:
            return None
        return event

    def list_all(self) -> list[DirectorEvent]:
        rows = self._conn.execute(
            "SELECT event_json FROM director_events ORDER BY created_turn ASC, created_at ASC"
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def resolve_active(
        self,
        event_id: str,
        *,
        turn_index: int,
        resolved_reason: str | None = None,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None or event.status != "active":
            return False
        updated = event.model_copy(
            update={
                "status": "resolved",
                "resolved_reason": resolved_reason or event.resolved_reason,
                "resolved_turn": turn_index,
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

    def set_exposure(self, event_id: str, exposure: str, *, turn_index: int) -> bool:
        event = self.get_by_id(event_id)
        if event is None:
            return False
        updated = event.model_copy(
            update={
                "exposure": exposure,
                "last_presented_turn": turn_index if exposure != "hidden" else event.last_presented_turn,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def advance_progress(
        self,
        event_id: str,
        *,
        turn_index: int,
        current_clock: WorldClock,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None or event.status != "active":
            return False
        next_index = min(event.progress_index + 1, 2)
        next_clock = self._next_progress_clock(event, next_index, current_clock)
        updated = event.model_copy(
            update={
                "progress_index": next_index,
                "next_progress_clock": next_clock,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def auto_advance_due(
        self,
        event_id: str,
        *,
        turn_index: int,
        current_clock: WorldClock,
    ) -> str | None:
        event = self.get_by_id(event_id)
        if event is None or event.status != "active" or event.next_progress_clock is None:
            return None
        if _clock_index(current_clock) < _clock_index(event.next_progress_clock):
            return None
        if event.progress_index >= 2:
            self.resolve_active(
                event_id,
                turn_index=turn_index,
                resolved_reason="事件后台演进抵达最终阶段并自动收束。",
            )
            return "resolved"
        self.advance_progress(
            event_id,
            turn_index=turn_index,
            current_clock=current_clock,
        )
        advanced = self.get_by_id(event_id)
        if advanced is not None and advanced.progress_index >= 2:
            self.resolve_active(
                event_id,
                turn_index=turn_index,
                resolved_reason="事件后台演进抵达最终阶段并自动收束。",
            )
            return "resolved"
        return "advanced"

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
        return self._list_by_statuses([status])

    def _list_by_statuses(self, statuses: list[str]) -> list[DirectorEvent]:
        placeholders = ", ".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT event_json FROM director_events WHERE status IN ({placeholders}) ORDER BY updated_at DESC",
            tuple(statuses),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def _resolve_other_active(self, event_id: str, turn_index: int) -> None:
        active = self.get_active()
        if active is None or active.event_id == event_id:
            return
        updated = active.model_copy(
            update={
                "status": "resolved",
                "resolved_reason": "被新的导演事件替换",
                "resolved_turn": turn_index,
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
        try:
            return DirectorEvent.model_validate(data)
        except ValidationError:
            return DirectorEvent.model_validate(_legacy_event_data(data))

    @staticmethod
    def _next_progress_clock(
        event: DirectorEvent,
        progress_index: int,
        current_clock: WorldClock,
    ) -> WorldClock | None:
        if progress_index >= 2:
            return current_clock
        interval_index = min(progress_index, len(event.milestone_interval_steps) - 1)
        return current_clock.advance_phase(event.milestone_interval_steps[interval_index])


def _clock_index(clock: WorldClock) -> int:
    from app.domain.current_state.schemas import PHASE_ORDER

    return (clock.day - 1) * len(PHASE_ORDER) + PHASE_ORDER.index(clock.phase)


def _legacy_event_data(data: dict) -> dict:
    title = str(data.get("title") or "旧导演事件")
    old_status = str(data.get("status") or "active")
    status = "active" if old_status == "active" else "resolved"
    premise = str(data.get("premise") or data.get("backstage_premise") or title)
    related_npcs = [str(value) for value in data.get("related_npcs") or [] if str(value).strip()]
    location_constraints = [
        str(value) for value in data.get("location_constraints") or [] if str(value).strip()
    ]
    actors = [str(value) for value in data.get("actors") or related_npcs if str(value).strip()]
    objective = str(
        data.get("objective")
        or data.get("driving_tension")
        or data.get("resolved_reason")
        or premise
    )
    bridge_anchors = _unique_nonempty(
        [
            *(data.get("bridge_anchors") or []),
            *location_constraints,
            *related_npcs,
        ]
    )
    if not bridge_anchors:
        bridge_anchors = [title]

    milestones = data.get("milestones")
    if not isinstance(milestones, list) or len(milestones) != 3:
        driving_tension = str(data.get("driving_tension") or objective)
        resolved_reason = str(data.get("resolved_reason") or f"{title}形成明确结果。")
        milestones = [premise, driving_tension, resolved_reason]

    entries = []
    for entry in data.get("entry_points") or []:
        if not isinstance(entry, dict):
            continue
        trigger_anchors = _unique_nonempty(
            [
                *(entry.get("trigger_anchors") or []),
                *(entry.get("natural_contexts") or []),
                *(entry.get("involved_npcs") or []),
                *bridge_anchors,
            ]
        )
        summary = str(entry.get("summary") or entry.get("visible_sign") or title)
        pressure = str(entry.get("pressure") or entry.get("immediate_effect") or summary)
        entries.append(
            {
                "trigger_anchors": trigger_anchors or bridge_anchors,
                "visible_sign": summary,
                "immediate_effect": pressure,
            }
        )
    if not entries:
        entries = [
            {
                "trigger_anchors": bridge_anchors,
                "visible_sign": f"{title}在周边显露出可察觉征兆。",
                "immediate_effect": f"{title}造成局部秩序或信息流动变化。",
            }
        ]

    exposure = data.get("exposure")
    if exposure not in {"hidden", "foreshadowed", "engaged"}:
        exposure = "engaged" if data.get("player_involved") else "hidden"

    return {
        "event_id": data.get("event_id"),
        "title": title,
        "status": status,
        "premise": premise,
        "actors": actors,
        "objective": objective,
        "bridge_anchors": bridge_anchors,
        "milestones": milestones,
        "milestone_interval_steps": data.get("milestone_interval_steps") or [3, 3],
        "next_progress_clock": data.get("next_progress_clock"),
        "progress_index": min(max(int(data.get("progress_index") or 0), 0), 2),
        "exposure": exposure,
        "entry_points": entries,
        "last_presented_turn": data.get("last_presented_turn"),
        "resolved_reason": data.get("resolved_reason") or data.get("suspended_reason"),
        "resolved_turn": data.get("resolved_turn"),
        "created_turn": data.get("created_turn") or 0,
        "updated_turn": data.get("updated_turn"),
        "created_at": data.get("created_at") or utc_now(),
        "updated_at": data.get("updated_at") or utc_now(),
    }


def _unique_nonempty(values: list) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result
