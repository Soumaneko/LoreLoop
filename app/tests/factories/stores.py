from __future__ import annotations

import sqlite3
from functools import partial
from pathlib import Path

from app.domain.confirmed_history.store import ConfirmedHistoryStore
from app.domain.continuity.store import ContinuityStore
from app.domain.current_state.store import CurrentStateStore
from app.domain.director.log_store import RecentDirectorLogStore
from app.domain.director_event.store import DirectorEventStore
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.scene_summary.store import SceneSummaryStore
from app.domain.short_term_memory.schemas import ShortTermMemoryStore
from app.domain.world_setting.store import WorldSettingStore
from app.runtime.sqlite import connect_sqlite


def _open_store(
    store_type,
    db_path: str | Path | sqlite3.Connection = ":memory:",
    **kwargs,
):
    owns_connection = not isinstance(db_path, sqlite3.Connection)
    connection = connect_sqlite(db_path) if owns_connection else db_path
    return store_type(connection, owns_connection=owns_connection, **kwargs)


open_current_state_store = partial(_open_store, CurrentStateStore)
open_continuity_store = partial(_open_store, ContinuityStore)
open_short_term_store = partial(_open_store, ShortTermMemoryStore)
open_scene_summary_store = partial(_open_store, SceneSummaryStore)
open_confirmed_history_store = partial(_open_store, ConfirmedHistoryStore)
open_npc_profile_store = partial(_open_store, NPCProfileStore)
open_director_event_store = partial(_open_store, DirectorEventStore)
open_director_npc_store = partial(_open_store, DirectorNPCStore)
open_director_log_store = partial(_open_store, RecentDirectorLogStore)
open_world_setting_store = partial(_open_store, WorldSettingStore)
