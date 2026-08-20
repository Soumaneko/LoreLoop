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
from .paths import CampaignPaths


class CampaignUnitOfWork:
    """Owns the lifecycle of every campaign SQLite connection.

    Domain stores no longer open or close their own connections. They receive a
    live ``sqlite3.Connection`` injected by this unit of work, which lazily opens
    one connection per physical database and closes them all when the unit exits.
    A ``write`` unit commits every opened connection on clean exit; a ``read``
    unit (or any unit that observed an exception) rolls back pending work.
    """

    def __init__(self, paths: CampaignPaths, *, write: bool = False):
        self.paths = paths
        self.write = write
        self._conns: dict[str, sqlite3.Connection] = {}
        self._closed = False

    def _get_conn(self, db_path: Path) -> sqlite3.Connection:
        path_str = str(db_path)
        if path_str not in self._conns:
            self.paths.data_dir.mkdir(parents=True, exist_ok=True)
            self._conns[path_str] = connect_sqlite(db_path)
        return self._conns[path_str]

    @property
    def current_state(self) -> CurrentStateStore:
        return CurrentStateStore(self._get_conn(self.paths.state_db))

    @property
    def continuity(self) -> ContinuityStore:
        return ContinuityStore(self._get_conn(self.paths.continuity_db))

    @property
    def short_term(self) -> ShortTermMemoryStore:
        return ShortTermMemoryStore(
            self._get_conn(self.paths.short_term_db), max_items=5
        )

    @property
    def scene_summary(self) -> SceneSummaryStore:
        return SceneSummaryStore(self._get_conn(self.paths.scene_summary_db))

    @property
    def confirmed_history(self) -> ConfirmedHistoryStore:
        return ConfirmedHistoryStore(
            self._get_conn(self.paths.confirmed_history_db)
        )

    @property
    def npc_profile(self) -> NPCProfileStore:
        return NPCProfileStore(self._get_conn(self.paths.npc_profile_db))

    @property
    def director_event(self) -> DirectorEventStore:
        return DirectorEventStore(self._get_conn(self.paths.director_event_db))

    @property
    def director_npc(self) -> DirectorNPCStore:
        return DirectorNPCStore(self._get_conn(self.paths.director_npc_db))

    @property
    def director_log(self) -> RecentDirectorLogStore:
        return RecentDirectorLogStore(self._get_conn(self.paths.director_log_db))

    @property
    def world_setting(self) -> WorldSettingStore:
        return WorldSettingStore(self._get_conn(self.paths.world_setting_db))

    @property
    def core_memory(self) -> CoreMemoryStore:
        return CoreMemoryStore(self.paths.core_memory_json)

    @property
    def is_closed(self) -> bool:
        """True once ``close()`` has released all connections (idempotent)."""
        return self._closed

    def close(self) -> None:
        """Close every opened connection, releasing file/DB handles.

        Idempotent: a second call is a no-op. The session-lifetime unit held on
        ``CampaignRuntime`` / ``RuntimeServices`` is closed explicitly at
        session teardown and when switching campaigns.
        """
        if self._closed:
            return
        self._closed = True
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()

    def __enter__(self) -> "CampaignUnitOfWork":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Stores own their own transaction commits; the unit only mirrors a
        # top-level commit/rollback so a ``write`` unit never leaves a dangling
        # transaction. A commit failure is an integrity problem and must
        # propagate rather than look like success; a rollback failure on a
        # read/error path is tolerated (the transaction is abandoned anyway).
        if exc_type is None and self.write:
            for conn in self._conns.values():
                conn.commit()
        else:
            for conn in self._conns.values():
                try:
                    conn.rollback()
                except sqlite3.OperationalError:
                    pass
        self.close()
