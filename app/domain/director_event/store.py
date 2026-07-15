import json
import sqlite3
from app.runtime.sqlite import connect_sqlite, ensure_schema_version, transaction

from app.domain.current_state.schemas import WorldClock

from .schemas import (
    DirectorEvent,
    DirectorEventExposure,
    DirectorEventInterventionPlan,
    DirectorEventResolutionKind,
    InterventionOutcome,
    utc_now,
)


_EXPOSURE_RANK: dict[DirectorEventExposure, int] = {
    "hidden": 0,
    "foreshadowed": 1,
    "engaged": 2,
}


class DirectorEventStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect_sqlite(db_path)
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
        versions_table_exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("schema_versions",),
        ).fetchone()
        if versions_table_exists is not None:
            version_row = self._conn.execute(
                "SELECT version FROM schema_versions WHERE component = ?",
                ("director_event",),
            ).fetchone()
            if version_row is not None and int(version_row["version"]) != 4:
                raise RuntimeError(
                    "Unsupported DirectorEvent database schema: "
                    f"expected 4, got {version_row['version']}"
                )
        ensure_schema_version(self._conn, "director_event", 4)
        self._conn.commit()

    def save(self, event: DirectorEvent) -> str:
        now = utc_now()
        event = event.model_copy(update={"updated_at": now})
        with transaction(self._conn):
            if event.status == "active":
                self._resolve_other_active(
                    event.event_id,
                    event.role,
                    event.updated_turn or event.created_turn,
                )
            self._write_without_active_guard(event)
        return event.event_id

    def get_active(self, role: str) -> DirectorEvent | None:
        return next((event for event in self.list_active() if event.role == role), None)

    def list_active(self) -> list[DirectorEvent]:
        return self._list_by_statuses(["active"])

    def list_resolved(self, role: str | None = None) -> list[DirectorEvent]:
        events = self._list_by_statuses(["resolved"])
        return [event for event in events if role is None or event.role == role]

    def get_latest_resolved(self, role: str | None = None) -> DirectorEvent | None:
        events = self.list_resolved(role)
        return events[0] if events else None

    def get_recent_resolved(
        self,
        *,
        role: str | None = None,
        turn_index: int,
        cooldown_turns: int,
    ) -> DirectorEvent | None:
        event = self.get_latest_resolved(role)
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
        resolution_kind: DirectorEventResolutionKind | None = None,
        resolved_state: str | None = None,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None or event.status != "active":
            return False
        updated = event.model_copy(
            update={
                "status": "resolved",
                "resolved_reason": resolved_reason or event.resolved_reason,
                "resolution_kind": resolution_kind or event.resolution_kind,
                "resolved_state": resolved_state or event.resolved_state,
                "resolved_turn": turn_index,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def redirect_active(
        self,
        event_id: str,
        *,
        turn_index: int,
        current_clock: WorldClock,
        redirect_reason: str,
        evidence: list[str],
        plan: DirectorEventInterventionPlan,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None or event.status != "active":
            return False

        if event.route_mode != "default" or event.intervention_route is not None:
            return False
        route = plan.to_route(
            triggered_turn=turn_index,
            redirect_reason=redirect_reason,
            evidence=evidence,
            current_clock=current_clock,
        )
        updated = event.model_copy(
            update={
                "route_mode": "intervention",
                "intervention_route": route,
                "next_progress_clock": None,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def resolve_intervention(
        self,
        event_id: str,
        *,
        outcome: InterventionOutcome,
        turn_index: int,
        resolved_reason: str,
    ) -> bool:
        event = self.get_by_id(event_id)
        route = event.intervention_route if event is not None else None
        if (
            event is None
            or event.status != "active"
            or event.route_mode != "intervention"
            or route is None
            or route.status != "active"
        ):
            return False

        succeeded = outcome == "success"
        resolved_state = route.success_state if succeeded else route.failure_state
        updated_route = route.model_copy(
            update={
                "status": "succeeded" if succeeded else "failed",
                "resolved_turn": turn_index,
            }
        )
        updated = event.model_copy(
            update={
                "status": "resolved",
                "intervention_route": updated_route,
                "resolution_kind": (
                    "intervention_success"
                    if succeeded
                    else "intervention_failure"
                ),
                "resolved_state": resolved_state,
                "resolved_reason": resolved_reason,
                "resolved_turn": turn_index,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def auto_fail_intervention_due(
        self,
        event_id: str,
        *,
        turn_index: int,
        current_clock: WorldClock,
    ) -> bool:
        event = self.get_by_id(event_id)
        route = event.intervention_route if event is not None else None
        if (
            event is None
            or event.status != "active"
            or event.route_mode != "intervention"
            or route is None
            or route.status != "active"
            or _clock_index(current_clock) < _clock_index(route.deadline_clock)
        ):
            return False
        return self.resolve_intervention(
            event_id,
            outcome="failure",
            turn_index=turn_index,
            resolved_reason=(
                f"介入目标在 {route.duration_phases} 个时段内未达成："
                f"{route.failure_state}"
            ),
        )

    def touch(self, event_id: str, *, turn_index: int) -> bool:
        event = self.get_by_id(event_id)
        if event is None:
            return False
        updated = event.model_copy(update={"updated_turn": turn_index, "updated_at": utc_now()})
        self.save(updated)
        return True

    def replace_player_contact(
        self,
        event_id: str,
        *,
        exposure: DirectorEventExposure,
        player_contact_state: str,
        turn_index: int,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None:
            return False
        exposure_regressed = _EXPOSURE_RANK[exposure] < _EXPOSURE_RANK[event.exposure]
        effective_exposure = event.exposure if exposure_regressed else exposure
        effective_contact_state = (
            event.player_contact_state
            if exposure_regressed
            else player_contact_state
        )
        updated = DirectorEvent.model_validate(
            {
                **event.model_dump(mode="python"),
                "exposure": effective_exposure,
                "player_contact_state": effective_contact_state,
                "last_presented_turn": (
                    turn_index
                    if effective_exposure != "hidden" and not exposure_regressed
                    else event.last_presented_turn
                ),
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
        if (
            event is None
            or event.status != "active"
            or event.route_mode != "default"
        ):
            return False
        next_index = min(event.progress_index + 1, 3)
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
        if (
            event is None
            or event.status != "active"
            or event.route_mode != "default"
            or event.next_progress_clock is None
        ):
            return None
        if _clock_index(current_clock) < _clock_index(event.next_progress_clock):
            return None
        if event.progress_index >= 3:
            self.resolve_active(
                event_id,
                turn_index=turn_index,
                resolved_reason="事件后台演进抵达最终阶段并自动收束。",
                resolution_kind="default",
                resolved_state=event.milestones[2],
            )
            return "resolved"
        self.advance_progress(
            event_id,
            turn_index=turn_index,
            current_clock=current_clock,
        )
        advanced = self.get_by_id(event_id)
        if advanced is not None and advanced.progress_index >= 3:
            self.resolve_active(
                event_id,
                turn_index=turn_index,
                resolved_reason="事件后台演进抵达最终阶段并自动收束。",
                resolution_kind="default",
                resolved_state=advanced.milestones[2],
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

    def _resolve_other_active(self, event_id: str, role: str, turn_index: int) -> None:
        active = self.get_active(role)
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

    @staticmethod
    def _event_from_row(row) -> DirectorEvent:
        data = json.loads(row["event_json"])
        return DirectorEvent.model_validate(data)

    @staticmethod
    def _next_progress_clock(
        event: DirectorEvent,
        progress_index: int,
        current_clock: WorldClock,
    ) -> WorldClock | None:
        if progress_index >= 3:
            return current_clock
        interval_index = min(
            progress_index - 1,
            len(event.milestone_interval_steps) - 1,
        )
        return current_clock.advance_phase(event.milestone_interval_steps[interval_index])


def _clock_index(clock: WorldClock) -> int:
    from app.domain.current_state.schemas import PHASE_ORDER

    return (clock.day - 1) * len(PHASE_ORDER) + PHASE_ORDER.index(clock.phase)
