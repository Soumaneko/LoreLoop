from __future__ import annotations

import sqlite3
from pathlib import Path

from app.domain.confirmed_history.store import ConfirmedHistoryStore
from app.domain.continuity.store import ContinuityStore
from app.domain.core_memory.store import CoreMemoryStore
from app.domain.current_state.store import CurrentStateStore
from app.domain.director.log_store import RecentDirectorLogStore
from app.domain.director_event.store import DirectorEventStore
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.scene_summary.store import SceneSummaryStore
from app.domain.short_term_memory.schemas import ShortTermMemoryStore
from app.domain.world_setting.store import WorldSettingStore
from app.runtime.sqlite import connect_sqlite


def _resolve_conn(
    db_path: str | Path | sqlite3.Connection,
) -> tuple[sqlite3.Connection, bool]:
    """Return ``(conn, owns)``.

    When given a path (including ``:memory:``) the factory creates and owns the
    connection, so ``close()`` on the returned store closes it. When given an
    existing connection, ownership stays with the caller (e.g. a test sharing a
    connection or the Unit of Work), so ``close()`` is a no-op.
    """
    if isinstance(db_path, sqlite3.Connection):
        return db_path, False
    return connect_sqlite(db_path), True


def open_current_state_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> CurrentStateStore:
    conn, owns = _resolve_conn(db_path)
    return CurrentStateStore(conn, owns_connection=owns)


def open_continuity_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> ContinuityStore:
    conn, owns = _resolve_conn(db_path)
    return ContinuityStore(conn, owns_connection=owns)


def open_short_term_store(
    db_path: str | Path | sqlite3.Connection = ":memory:", *, max_items: int = 5
) -> ShortTermMemoryStore:
    conn, owns = _resolve_conn(db_path)
    return ShortTermMemoryStore(conn, owns_connection=owns, max_items=max_items)


def open_scene_summary_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> SceneSummaryStore:
    conn, owns = _resolve_conn(db_path)
    return SceneSummaryStore(conn, owns_connection=owns)


def open_confirmed_history_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> ConfirmedHistoryStore:
    conn, owns = _resolve_conn(db_path)
    return ConfirmedHistoryStore(conn, owns_connection=owns)


def open_npc_profile_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> NPCProfileStore:
    conn, owns = _resolve_conn(db_path)
    return NPCProfileStore(conn, owns_connection=owns)


def open_director_event_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> DirectorEventStore:
    conn, owns = _resolve_conn(db_path)
    return DirectorEventStore(conn, owns_connection=owns)


def open_director_npc_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> DirectorNPCStore:
    conn, owns = _resolve_conn(db_path)
    return DirectorNPCStore(conn, owns_connection=owns)


def open_director_log_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> RecentDirectorLogStore:
    conn, owns = _resolve_conn(db_path)
    return RecentDirectorLogStore(conn, owns_connection=owns)


def open_world_setting_store(
    db_path: str | Path | sqlite3.Connection = ":memory:",
) -> WorldSettingStore:
    conn, owns = _resolve_conn(db_path)
    return WorldSettingStore(conn, owns_connection=owns)


def open_core_memory_store(path: str | Path) -> CoreMemoryStore:
    return CoreMemoryStore(path)
