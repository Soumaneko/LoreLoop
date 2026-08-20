import pytest

from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime
from app.runtime.uow import CampaignUnitOfWork


def _paths(tmp_path):
    return CampaignPaths.from_data_dir(tmp_path / "save")


def test_connection_reuse_same_path(tmp_path):
    uow = CampaignUnitOfWork(_paths(tmp_path))
    first = uow._get_conn(uow.paths.state_db)
    second = uow._get_conn(uow.paths.state_db)
    assert first is second
    other = uow._get_conn(uow.paths.continuity_db)
    assert other is not first
    uow.close()


def test_close_is_idempotent(tmp_path):
    uow = CampaignUnitOfWork(_paths(tmp_path))
    uow.current_state
    uow.close()
    uow.close()


def test_write_unit_commits_on_clean_exit(tmp_path):
    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        conn = uow._get_conn(paths.state_db)
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES (1)")
    with CampaignUnitOfWork(paths, write=False) as uow:
        row = uow._get_conn(paths.state_db).execute(
            "SELECT x FROM t"
        ).fetchone()
    assert row is not None


def _with_table(uow, paths):
    conn = uow._get_conn(paths.state_db)
    conn.execute("CREATE TABLE IF NOT EXISTS t(x)")


def test_write_unit_rolls_back_on_exception(tmp_path):
    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        _with_table(uow, paths)
    with pytest.raises(RuntimeError):
        with CampaignUnitOfWork(paths, write=True) as uow:
            conn = uow._get_conn(paths.state_db)
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
    with CampaignUnitOfWork(paths, write=False) as uow:
        rows = uow._get_conn(paths.state_db).execute(
            "SELECT x FROM t"
        ).fetchall()
    assert rows == []


def test_exit_closes_opened_connections(tmp_path):
    import sqlite3

    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        conn = uow._get_conn(paths.state_db)
        conn.execute("CREATE TABLE t(x)")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_read_unit_does_not_commit(tmp_path):
    # A read unit (write=False) leaves uncommitted work rolled back.
    paths = _paths(tmp_path)
    with CampaignUnitOfWork(paths, write=True) as uow:
        _with_table(uow, paths)
    with CampaignUnitOfWork(paths, write=False) as uow:
        conn = uow._get_conn(paths.state_db)
        conn.execute("INSERT INTO t VALUES (1)")
    with CampaignUnitOfWork(paths, write=False) as uow:
        rows = uow._get_conn(paths.state_db).execute(
            "SELECT x FROM t"
        ).fetchall()
    assert rows == []


def test_runtime_reconfigure_and_close_release_old_uow(tmp_path):
    runtime = CampaignRuntime()
    old = runtime.stores
    old.current_state
    assert not old.is_closed

    runtime.reconfigure("new-campaign", _paths(tmp_path))
    assert old.is_closed

    runtime.close()
    assert runtime.stores.is_closed
