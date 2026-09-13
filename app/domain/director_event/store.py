import json
import sqlite3
from app.runtime.sqlite import ensure_exact_schema_version, transaction

from app.domain.director.schemas import DirectorResourceRef

from .schemas import (
    DirectorActorRef,
    DirectorEvent,
    DirectorEventStage,
    WorldImpact,
    utc_now,
)


class DirectorEventStore:
    def __init__(self, conn: sqlite3.Connection, *, owns_connection: bool = False):
        self._conn = conn
        self._owns_connection = owns_connection
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS director_events (
                event_id TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_turn INTEGER NOT NULL,
                updated_turn INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_exact_schema_version(self._conn, "director_event", 11)
        self._conn.commit()

    def save(self, event: DirectorEvent) -> str:
        now = utc_now()
        event = event.model_copy(update={"updated_at": now})
        self._write(event)
        return event.event_id

    def restore_snapshot(self, event: DirectorEvent) -> None:
        """Restore an exact pre-commit image during transaction recovery."""
        self._write(event)

    def list_active(self) -> list[DirectorEvent]:
        return self._list_by_stages(["hidden", "engaged"])

    def list_hidden(self) -> list[DirectorEvent]:
        return self._list_by_stages(["hidden"])

    def list_engaged(self) -> list[DirectorEvent]:
        return self._list_by_stages(["engaged"])

    def list_resolved(self) -> list[DirectorEvent]:
        return self._list_by_stages(["resolved"])

    def list_all(self) -> list[DirectorEvent]:
        rows = self._conn.execute(
            "SELECT event_json FROM director_events ORDER BY created_turn ASC, created_at ASC"
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def get_by_id(self, event_id: str) -> DirectorEvent | None:
        row = self._conn.execute(
            "SELECT event_json FROM director_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return self._event_from_row(row) if row else None

    def set_stage(
        self,
        event_id: str,
        stage: DirectorEventStage,
        *,
        turn_index: int,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None:
            return False
        updated = event.model_copy(
            update={
                "stage": stage,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def mark_hooks_consumed(
        self,
        event_id: str,
        hook_ids: list[str],
        *,
        turn_index: int,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None:
            return False
        consumed = set(hook_ids)
        hook_pool = [
            hook.model_copy(
                update={
                    "consumed": True,
                    "consumed_turn": (
                        hook.consumed_turn if hook.consumed else turn_index
                    ),
                }
            )
            if hook.hook_id in consumed
            else hook
            for hook in event.hook_pool
        ]
        updated = event.model_copy(
            update={
                "hook_pool": hook_pool,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def resolve_event(
        self,
        event_id: str,
        *,
        turn_index: int,
        resolved_reason: str,
        final_world_impact: WorldImpact | None = None,
    ) -> bool:
        event = self.get_by_id(event_id)
        if event is None or event.stage == "resolved":
            return False
        updated = event.model_copy(
            update={
                "stage": "resolved",
                "resolved_reason": resolved_reason,
                "resolved_turn": turn_index,
                "final_world_impact": final_world_impact,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        return True

    def bind_actor(
        self,
        event_id: str,
        *,
        actor_name: str,
        actor_ref: DirectorResourceRef,
        turn_index: int,
    ) -> DirectorEvent:
        event = self.get_by_id(event_id)
        if event is None or event.stage == "resolved":
            raise ValueError("actor can only be bound to an active DirectorEvent")
        actors = list(event.actors)
        clean_name = actor_name.strip()
        new_actor = DirectorActorRef(name=clean_name, ref=actor_ref)
        replaced = False
        for index, existing in enumerate(actors):
            if existing.ref is not None and existing.ref == actor_ref:
                return event
            if clean_name and existing.name == clean_name and existing.ref is None:
                actors[index] = new_actor
                replaced = True
                break
        if not replaced and clean_name and not any(
            existing.name == clean_name and existing.ref == actor_ref
            for existing in actors
        ):
            actors.append(new_actor)
        updated = event.model_copy(
            update={
                "actors": actors,
                "updated_turn": turn_index,
                "updated_at": utc_now(),
            }
        )
        self.save(updated)
        persisted = self.get_by_id(event_id)
        if persisted is None:
            raise RuntimeError("bound DirectorEvent disappeared after save")
        return persisted

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM director_events").fetchone()
        return int(row[0])

    def delete_by_id(self, event_id: str) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "DELETE FROM director_events WHERE event_id = ?",
                (event_id,),
            )

    def _list_by_stages(self, stages: list[str]) -> list[DirectorEvent]:
        placeholders = ", ".join("?" for _ in stages)
        rows = self._conn.execute(
            f"SELECT event_json FROM director_events WHERE stage IN ({placeholders}) ORDER BY updated_at DESC",
            tuple(stages),
        ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def _write(self, event: DirectorEvent) -> None:
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO director_events (
                    event_id, stage, event_json, created_turn, updated_turn, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.stage,
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

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()

    def __enter__(self) -> "DirectorEventStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()
