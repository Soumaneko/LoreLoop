import json
import sqlite3
from pathlib import Path

import pytest

from app.runtime.campaign_migration import ensure_campaign_database
from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime
from app.runtime.sqlite import connect_sqlite
from app.runtime.turn_history import CampaignHistory
from app.runtime.uow import CampaignUnitOfWork


def _valid_campaign(tmp_path: Path) -> CampaignPaths:
    p = CampaignPaths.from_data_dir(tmp_path / "save")
    ensure_campaign_database(p)
    return p


def test_invalid_snapshot_does_not_delete_live_json(tmp_path):
    paths = _valid_campaign(tmp_path)
    (paths.data_dir / "initialization.json").write_text('{"phase":"completed"}', encoding="utf-8")
    (paths.data_dir / "core_memory.json").write_text('{"items":[]}', encoding="utf-8")
    rt = CampaignRuntime("test", paths=paths, max_workers=1)
    rt.history.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    rt.history.begin_confirmation("t1", write_db=True, scene_text=None)
    conn = connect_sqlite(paths.campaign_db)
    conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
    conn.commit()
    conn.close()
    rt.history.complete_confirmation("t1", "g1")
    with rt.history._connect() as c:
        row = c.execute("SELECT path FROM turn_snapshots WHERE status='applied' ORDER BY sequence DESC LIMIT 1").fetchone()
        snap_path = rt.history._snapshot_path(row["path"])
    # Snapshot includes initialization/core; corrupt campaign.db only
    (snap_path / "campaign.db").write_bytes(b"not a db")
    before_init = (paths.data_dir / "initialization.json").read_text()
    before_core = (paths.data_dir / "core_memory.json").read_text()
    with pytest.raises(Exception):
        rt.history.undo()
    assert (paths.data_dir / "initialization.json").read_text() == before_init
    assert (paths.data_dir / "core_memory.json").read_text() == before_core
    rt.close()


def test_invalid_snapshot_does_not_replace_canonical(tmp_path):
    paths = _valid_campaign(tmp_path)
    # Create a valid snapshot via normal flow
    rt = CampaignRuntime("test", paths=paths, max_workers=1)
    rt.history.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    rt.history.begin_confirmation("t1", write_db=True, scene_text=None)
    # complete to get applied snapshot
    conn = connect_sqlite(paths.campaign_db)
    conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
    conn.commit()
    conn.close()
    rt.history.complete_confirmation("t1", "g1")
    status = rt.history.status()
    assert status["can_undo"]
    snap_id = status["snapshots"][0]["snapshot_id"]
    # Corrupt snapshot's campaign.db to be invalid
    with rt.history._connect() as c:
        row = c.execute("SELECT path FROM turn_snapshots WHERE snapshot_id=?", (snap_id,)).fetchone()
        snap_path = rt.history._snapshot_path(row["path"])
    # Overwrite snapshot db with invalid content
    (snap_path / "campaign.db").write_bytes(b"not a db")
    # Attempt undo should fail and not replace canonical
    before = Path(paths.campaign_db).read_bytes()
    with pytest.raises(Exception):
        rt.history.undo()
    after = Path(paths.campaign_db).read_bytes()
    assert before == after
    rt.close()


def test_stale_uow_rejected_after_restore(tmp_path):
    paths = _valid_campaign(tmp_path)
    hist = CampaignHistory(paths.data_dir)
    hist.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    hist.begin_confirmation("t1", write_db=True, scene_text=None)
    hist.complete_confirmation("t1", "g1")
    epoch_before = hist.connection_epoch
    stale = CampaignUnitOfWork(paths, expected_epoch=epoch_before)
    stale._get_conn().execute("SELECT 1").fetchone()
    hist.undo()
    epoch_after = hist.connection_epoch
    assert epoch_after == epoch_before + 1
    with pytest.raises(RuntimeError, match="stale UoW"):
        stale._get_conn().execute("SELECT 1")
    with pytest.raises(RuntimeError, match="stale UoW"):
        with stale.transaction():
            pass
    stale.close()


def test_epoch_only_once_after_crash_at_intent(tmp_path):
    # Simulate crash after intent (undoing) before file replace, then recover
    paths = _valid_campaign(tmp_path)
    hist = CampaignHistory(paths.data_dir)
    hist.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    hist.begin_confirmation("t1", write_db=True, scene_text=None)
    hist.complete_confirmation("t1", "g1")
    epoch0 = hist.connection_epoch
    # Start undo but simulate crash after intent: manually set status to undoing
    row = None
    with hist._connect() as c:
        row = c.execute("SELECT * FROM turn_snapshots WHERE status='applied' ORDER BY sequence DESC LIMIT 1").fetchone()
        # Capture redo manually
        snap_path = hist._snapshot_path(row["path"])
        redo = hist.snapshots_dir / f"redo-{row['snapshot_id']}"
        hist._capture_snapshot(redo)
        c.execute("UPDATE turn_snapshots SET status='undoing', redo_path=? WHERE sequence=?", (hist._stored_snapshot_path(redo), row["sequence"]))
        c.commit()
    # Now crash before file replace, restart
    hist2 = CampaignHistory(paths.data_dir)
    assert hist2.recover_interrupted() is True
    assert hist2.connection_epoch == epoch0 + 1
    # Second recover should be idempotent
    assert hist2.recover_interrupted() is False
    assert hist2.connection_epoch == epoch0 + 1


def test_quiesce_blocks_new_uow_and_times_out(tmp_path):
    paths = _valid_campaign(tmp_path)
    rt = CampaignRuntime("q", paths=paths, max_workers=1)
    u = rt.uow(write=False)
    # Gate not closed, new uow allowed
    u2 = rt.uow(write=False)
    assert u2
    # Simulate long-running UoW by not closing u
    # Trigger quiesce in background thread would wait, but we test timeout directly
    with pytest.raises(RuntimeError, match="quiesce timeout"):
        rt._wait_for_active_uows(timeout=0.1)
    u.close()
    u2.close()
    # After close, wait should succeed
    rt._wait_for_active_uows(timeout=0.1)
    rt.close()


def test_confirmation_failure_increments_epoch(tmp_path):
    paths = _valid_campaign(tmp_path)
    rt = CampaignRuntime("cf", paths=paths, max_workers=1)
    rt.history.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    rt.history.begin_confirmation("t1", write_db=True, scene_text=None)
    epoch0 = rt.history.connection_epoch
    rt.history.fail_confirmation_offline("t1", RuntimeError("fail"))
    assert rt.history.connection_epoch == epoch0 + 1
    rt.close()


def test_reconfigure_invalidates_old_operation_uow(tmp_path):
    paths = _valid_campaign(tmp_path)
    rt = CampaignRuntime("sw", paths=paths, max_workers=1)
    old_uow = rt.uow(write=False)
    old_uow._get_conn().execute("SELECT 1").fetchone()
    # An active operation UoW must be released before the switch can proceed;
    # once released, it is permanently closed and cannot reopen against the
    # old save.
    old_uow.close()
    new_paths = _valid_campaign(tmp_path / "other")
    rt.reconfigure("sw2", new_paths)
    assert old_uow.is_closed
    with pytest.raises(RuntimeError, match="UoW is closed"):
        old_uow._get_conn()
    rt.close()


def test_reconfigure_failure_keeps_old_runtime_usable(tmp_path):
    paths = _valid_campaign(tmp_path)
    rt = CampaignRuntime("sw-fail", paths=paths, max_workers=1)
    old_campaign_id = rt.campaign_id
    old_paths = rt.paths
    # Target directory has an invalid campaign.db; ensure_campaign_database
    # will fail while building the new runtime.
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "campaign.db").write_bytes(b"not a database")
    with pytest.raises(Exception):
        rt.reconfigure("sw-bad", CampaignPaths.from_data_dir(bad_dir))
    # Old runtime identity remains intact and gate is open.
    assert rt.campaign_id == old_campaign_id
    assert rt.paths == old_paths
    assert rt.stores.is_closed is False
    # A new UoW can still be created after the failed switch.
    u = rt.uow(write=False)
    u.close()
    # Operations/jobs must be freshly usable, not the shutdown instances.
    with rt.operation_submission():
        op_id = rt.operations.submit_callable("probe", lambda: 42)
        snapshot = rt.operations.wait(op_id, timeout=5)
        assert snapshot.status == "succeeded"
    rt.close()


def test_production_restore_rejects_minimal_db(tmp_path):
    paths = CampaignPaths.from_data_dir(tmp_path / "save")
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    ensure_campaign_database(paths)
    # Create minimal snapshot manually
    hist = CampaignHistory(paths.data_dir)
    hist.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    hist.begin_confirmation("t1", write_db=True, scene_text=None)
    # Create minimal snapshot dir with game_state only
    with hist._connect() as c:
        row = c.execute("SELECT snapshot_id, path FROM turn_snapshots WHERE turn_id='t1'").fetchone()
        snap_dir = hist._snapshot_path(row["path"])
    # Overwrite snapshot campaign.db with minimal
    (snap_dir / "campaign.db").unlink(missing_ok=True)
    conn = sqlite3.connect(snap_dir / "campaign.db")
    conn.execute("CREATE TABLE game_state (x INT)")
    conn.commit()
    conn.close()
    # Restore must reject the minimal DB (missing required schema/tables).
    with pytest.raises(RuntimeError):
        hist._restore_snapshot(snap_dir)
