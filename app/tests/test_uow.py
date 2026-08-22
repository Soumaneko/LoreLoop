import sqlite3

import pytest

from app.runtime import uow as uow_module
from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime
from app.runtime.uow import CampaignUnitOfWork

STORE_NAMES = (
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
)


def _paths(tmp_path):
    return CampaignPaths.from_data_dir(tmp_path / "save")


def test_single_shared_connection(tmp_path):
    uow = CampaignUnitOfWork(_paths(tmp_path))
    first = uow._get_conn()
    second = uow._get_conn()
    assert first is second
    uow.close()


def test_all_stores_share_one_connection(tmp_path):
    uow = CampaignUnitOfWork(_paths(tmp_path))
    conn = uow._get_conn()
    for name in STORE_NAMES:
        store = getattr(uow, name)
        assert getattr(store, "_conn", None) is conn or getattr(store, "_db", None) is conn
    uow.close()


def test_close_is_idempotent(tmp_path):
    uow = CampaignUnitOfWork(_paths(tmp_path))
    uow.current_state
    uow.close()
    uow.close()


def test_bootstrap_runs_before_business_transaction(tmp_path):
    uow = CampaignUnitOfWork(_paths(tmp_path))
    with uow.transaction():
        tables = {
            row[0]
            for row in uow._get_conn().execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "schema_versions" in tables
    assert "continuity_items" in tables
    assert "npc_profiles" in tables
    uow.close()


def test_transaction_commits_atomically(tmp_path):
    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        with uow.transaction():
            uow._get_conn().execute(
                "CREATE TABLE t(x INTEGER PRIMARY KEY)"
            )
            uow._get_conn().execute("INSERT INTO t VALUES (1)")
            uow._get_conn().execute("INSERT INTO t VALUES (2)")
    with CampaignUnitOfWork(paths) as uow:
        rows = uow._get_conn().execute("SELECT x FROM t").fetchall()
    assert [row[0] for row in rows] == [1, 2]


def test_transaction_rolls_back_on_exception(tmp_path):
    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        uow._get_conn().execute("CREATE TABLE t(x INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError):
        with CampaignUnitOfWork(paths, write=True) as uow:
            with uow.transaction():
                uow._get_conn().execute("INSERT INTO t VALUES (1)")
                raise RuntimeError("boom")
    with CampaignUnitOfWork(paths) as uow:
        rows = uow._get_conn().execute("SELECT x FROM t").fetchall()
    assert rows == []


def test_write_unit_commits_on_clean_exit(tmp_path):
    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        conn = uow._get_conn()
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (1)")
    with CampaignUnitOfWork(paths) as uow:
        row = uow._get_conn().execute("SELECT x FROM t").fetchone()
    assert row is not None


def test_write_unit_rolls_back_on_exception(tmp_path):
    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        uow._get_conn().execute("CREATE TABLE t(x)")
    with pytest.raises(RuntimeError):
        with CampaignUnitOfWork(paths, write=True) as uow:
            conn = uow._get_conn()
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
    with CampaignUnitOfWork(paths) as uow:
        rows = uow._get_conn().execute("SELECT x FROM t").fetchall()
    assert rows == []


def test_exit_closes_opened_connections(tmp_path):
    import sqlite3

    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        conn = uow._get_conn()
        conn.execute("CREATE TABLE t(x)")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_read_unit_does_not_commit(tmp_path):
    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        uow._get_conn().execute("CREATE TABLE t(x)")
    with CampaignUnitOfWork(paths, write=False) as uow:
        conn = uow._get_conn()
        conn.execute("INSERT INTO t VALUES (1)")
    with CampaignUnitOfWork(paths) as uow:
        rows = uow._get_conn().execute("SELECT x FROM t").fetchall()
    assert rows == []


def test_closed_uow_cannot_reopen(tmp_path):
    uow = CampaignUnitOfWork(_paths(tmp_path))
    uow.current_state
    uow.close()
    assert uow.is_closed
    with pytest.raises(RuntimeError, match="UoW is closed"):
        uow._get_conn()
    with pytest.raises(RuntimeError, match="UoW is closed"):
        with uow:
            pass


def test_bare_uow_is_closed_after_context_exit(tmp_path):
    uow = CampaignUnitOfWork(_paths(tmp_path))
    with uow:
        uow._get_conn().execute("SELECT 1").fetchone()
    assert uow.is_closed
    with pytest.raises(RuntimeError, match="UoW is closed"):
        uow._get_conn()
    with pytest.raises(RuntimeError, match="UoW is closed"):
        with uow:
            pass


def test_runtime_reconfigure_and_close_release_old_uow(tmp_path):
    runtime = CampaignRuntime()
    old = runtime.stores
    old.current_state
    assert not old.is_closed

    runtime.reconfigure("new-campaign", _paths(tmp_path))
    assert old.is_closed

    runtime.close()
    assert runtime.stores.is_closed


class _CommitFailingConn:
    """Proxy that fails every commit, simulating a real commit failure."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self):
        raise sqlite3.OperationalError("injected commit failure")


class _RecordingRuntimeRef:
    def __init__(self):
        self.unregistered = []

    def _unregister_uow(self, uow):
        self.unregistered.append(uow)


def test_commit_failure_still_releases_resources(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    captured = {}
    real_connect = uow_module.connect_sqlite

    def failing_connect(db_path, **kwargs):
        conn = real_connect(db_path, **kwargs)
        captured["real_conn"] = conn
        return _CommitFailingConn(conn)

    monkeypatch.setattr(uow_module, "connect_sqlite", failing_connect)
    ref = _RecordingRuntimeRef()
    uow = CampaignUnitOfWork(paths, write=True, runtime_ref=ref)
    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        with uow:
            conn = uow._get_conn()
            # DDL autocommits; the INSERT opens the transaction whose final
            # commit() hits the injected failure inside __exit__.
            conn.execute("CREATE TABLE t(x)")
            conn.execute("INSERT INTO t VALUES (1)")

    # Commit raised inside __exit__, yet the UoW must still be fully released:
    assert uow.is_closed
    assert ref.unregistered == [uow]
    assert uow._conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        captured["real_conn"].execute("SELECT 1")