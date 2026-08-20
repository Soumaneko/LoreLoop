from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

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
    """Owns one SQLite connection shared by every canonical domain store.

    Phase 2 consolidates the ten canonical databases into a single
    ``campaign.db``. A unit opens one connection, boots all ten stores against
    it, and hands every store the same connection so a confirmation runs as one
    ``BEGIN IMMEDIATE ... COMMIT`` transaction.

    Store instances are cached inside the unit so schema bootstrap runs once.
    Bootstrap always finishes before the first business write transaction, so a
    store's ``_init_*`` commit can never close an outer transaction.
    """

    def __init__(self, paths: CampaignPaths, *, write: bool = False):
        self.paths = paths
        self.write = write
        self._conn: sqlite3.Connection | None = None
        self._stores: dict[str, object] = {}
        self._bootstrapped = False
        self._closed = False

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.paths.data_dir.mkdir(parents=True, exist_ok=True)
            self._conn = connect_sqlite(self.paths.campaign_db)
        return self._conn

    def _store(self, name: str, factory) -> object:
        if name not in self._stores:
            self._stores[name] = factory(self._get_conn())
        return self._stores[name]

    def _ensure_bootstrapped(self) -> None:
        """Instantiate every store so all schema bootstrap runs up front."""
        if self._bootstrapped:
            return
        for name in (
            "current_state",
            "continuity",
            "short_term",
            "scene_summary",
            "confirmed_history",
            "npc_profile",
            "director_event",
            "director_npc",
            "director_log",
            "world_setting",
        ):
            getattr(self, name)
        self._bootstrapped = True

    @property
    def current_state(self) -> CurrentStateStore:
        return self._store("current_state", CurrentStateStore)  # type: ignore[arg-type]

    @property
    def continuity(self) -> ContinuityStore:
        return self._store("continuity", ContinuityStore)  # type: ignore[arg-type]

    @property
    def short_term(self) -> ShortTermMemoryStore:
        def factory(conn: sqlite3.Connection) -> ShortTermMemoryStore:
            return ShortTermMemoryStore(conn, max_items=5)

        return self._store("short_term", factory)  # type: ignore[arg-type]

    @property
    def scene_summary(self) -> SceneSummaryStore:
        return self._store("scene_summary", SceneSummaryStore)  # type: ignore[arg-type]

    @property
    def confirmed_history(self) -> ConfirmedHistoryStore:
        return self._store("confirmed_history", ConfirmedHistoryStore)  # type: ignore[arg-type]

    @property
    def npc_profile(self) -> NPCProfileStore:
        return self._store("npc_profile", NPCProfileStore)  # type: ignore[arg-type]

    @property
    def director_event(self) -> DirectorEventStore:
        return self._store("director_event", DirectorEventStore)  # type: ignore[arg-type]

    @property
    def director_npc(self) -> DirectorNPCStore:
        return self._store("director_npc", DirectorNPCStore)  # type: ignore[arg-type]

    @property
    def director_log(self) -> RecentDirectorLogStore:
        return self._store("director_log", RecentDirectorLogStore)  # type: ignore[arg-type]

    @property
    def world_setting(self) -> WorldSettingStore:
        return self._store("world_setting", WorldSettingStore)  # type: ignore[arg-type]

    @property
    def core_memory(self) -> CoreMemoryStore:
        return CoreMemoryStore(self.paths.core_memory_json)

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """One top-level write transaction with ``BEGIN IMMEDIATE``.

        Schema bootstrap always completes before the transaction starts. When
        ``immediate`` is false (read path) or a transaction is already open,
        no new top-level transaction is started; nested store writes degrade to
        SAVEPOINTs through the shared ``transaction()`` helper.
        """
        self._ensure_bootstrapped()
        conn = self._get_conn()
        if not immediate or conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()

    @property
    def is_closed(self) -> bool:
        """True once ``close()`` has released the shared connection."""
        return self._closed

    def close(self) -> None:
        """Close the shared connection. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._conn is not None:
            self._conn.close()
        self._conn = None
        self._stores.clear()

    def __enter__(self) -> "CampaignUnitOfWork":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Stores participate in the top-level transaction through SAVEPOINTs;
        # the unit mirrors a final commit/rollback as a safety net so a ``write``
        # unit never leaves a dangling transaction.
        if self._conn is not None:
            if exc_type is None and self.write:
                try:
                    self._conn.commit()
                except sqlite3.OperationalError:
                    pass
            else:
                try:
                    self._conn.rollback()
                except sqlite3.OperationalError:
                    pass
        self.close()