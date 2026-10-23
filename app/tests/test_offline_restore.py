import json
import sqlite3
from pathlib import Path

import pytest

from app.runtime.campaign_migration import ensure_campaign_database
from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime
from app.runtime.turn_history import CampaignHistory
from app.runtime.uow import CampaignUnitOfWork


def _valid_campaign(tmp_path: Path) -> CampaignPaths:
    p = CampaignPaths.from_data_dir(tmp_path / "save")
    ensure_campaign_database(p)
    return p


def test_undo_redo_preserves_unrelated_database_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = _valid_campaign(Path("."))
    files = {
        paths.data_dir / name: f"original {name}".encode()
        for name in ("current_state.db", "notes.db", "notes.db-wal")
    }
    for path, content in files.items():
        path.write_bytes(content)
    history = CampaignHistory(paths.data_dir)
    history.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    history.begin_confirmation("t1", write_db=True, scene_text=None)
    with history._campaign_connect() as campaign:
        campaign.execute("BEGIN")
        history.record_confirmation(campaign, "t1", scene_text="场景正文")
    history.complete_confirmation("t1", "g1")

    history.undo()
    for path, content in files.items():
        assert path.read_bytes() == content
    history.redo()
    for path, content in files.items():
        assert path.read_bytes() == content


@pytest.mark.parametrize("damage", ["invalid_database", "missing_campaign"])
def test_invalid_snapshot_preserves_progress_and_live_json(tmp_path, damage):
    paths = _valid_campaign(tmp_path)
    core = paths.data_dir / "core_memory.json"
    core.write_text('{"items":[]}', encoding="utf-8")
    rt = CampaignRuntime("test", paths=paths, max_workers=1)
    rt.history.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    rt.history.begin_confirmation("t1", write_db=True, scene_text=None)
    with rt.history._campaign_connect() as campaign:
        campaign.execute("BEGIN")
        rt.history.record_confirmation(campaign, "t1", scene_text="场景正文")
    rt.history.complete_confirmation("t1", "g1")
    core.write_text(json.dumps({"items": [{
        "id": "current", "kind": "world_invariant", "strength": "hard_constraint",
        "source": "player", "content": "当前世界设定",
    }]}), encoding="utf-8")
    with rt.history._connect() as c:
        row = c.execute("SELECT path FROM turn_snapshots WHERE turn_id='t1'").fetchone()
        snap_path = rt.history._snapshot_path(row["path"])
    if damage == "invalid_database":
        (snap_path / "campaign.db").write_bytes(b"not a db")
    else:
        (snap_path / "manifest.json").write_text(json.dumps({"files": ["core_memory.json"]}))
    before = {path: path.read_bytes() for path in (paths.campaign_db, core)}
    with pytest.raises(RuntimeError):
        rt.undo()
    assert {path: path.read_bytes() for path in before} == before
    assert rt.history.get_transaction("t1")["status"] == "completed"
    with rt.uow() as uow:
        assert uow.current_state.load() is None
    rt.close()
    reopened = CampaignRuntime("reopened", paths=paths)
    reopened.close()


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
    with rt._pause_connections():
        with pytest.raises(RuntimeError, match="offline restore"):
            rt.uow()
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
    with pytest.raises(RuntimeError, match="UoW is closed"):
        with old_uow.transaction():
            pass
    with rt.uow() as fresh:
        with fresh.current_state as store:
            assert store.load() is None
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


def test_reopen_finishes_database_and_json_restore_together(tmp_path, monkeypatch):
    import shutil
    from app.runtime import turn_history

    paths = _valid_campaign(tmp_path)
    core = paths.data_dir / "core_memory.json"
    core.write_text('{"items":[]}')
    history = CampaignHistory(paths.data_dir)
    history.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id":"t1"})
    history.begin_confirmation("t1", write_db=True, scene_text=None)
    with history._campaign_connect() as conn:
        conn.execute("BEGIN")
        history.record_confirmation(conn, "t1", scene_text="正文")
    history.complete_confirmation("t1", "g1")
    core.write_text('{"items":[{"id":"changed"}]}')
    original = turn_history.install_snapshot

    def interrupt_after_database(data_dir, source):
        shutil.copy2(source / "campaign.db", data_dir / "campaign.db")
        raise OSError("interrupted before JSON replacement")

    monkeypatch.setattr(turn_history, "install_snapshot", interrupt_after_database)
    with pytest.raises(OSError, match="interrupted before JSON"):
        history.undo()
    assert json.loads(core.read_text())["items"]
    monkeypatch.setattr(turn_history, "install_snapshot", original)
    reopened = CampaignHistory(paths.data_dir)
    assert reopened.recover_interrupted() is True
    assert json.loads(core.read_text()) == {"items":[]}
    assert reopened.get_transaction("t1")["status"] == "awaiting_confirm"
    assert reopened.status()["can_redo"] is True
    assert reopened.recover_interrupted() is False
    reopened.redo()
    assert json.loads(core.read_text())["items"] == [{"id":"changed"}]


@pytest.mark.parametrize("operation", ["undo", "redo"])
@pytest.mark.parametrize("installed", [False, True])
def test_interrupted_history_retry_only_completes_original_turn(tmp_path, monkeypatch, operation, installed):
    from app.runtime import turn_history

    history = CampaignHistory(_valid_campaign(tmp_path).data_dir)
    for index in (1, 2):
        turn_id = f"turn-{index}"
        history.save_pending(turn_id=turn_id, kind="turn", group_id=turn_id,
                             turn_index=index, payload={})
        history.begin_confirmation(turn_id, write_db=True, scene_text=turn_id)
        with history._campaign_connect() as conn:
            conn.execute("BEGIN")
            history.record_confirmation(conn, turn_id, scene_text=turn_id)
        history.complete_confirmation(turn_id, turn_id)
    if operation == "redo":
        history.undo()
        history.undo()
    expected_turn = "turn-2" if operation == "undo" else "turn-1"
    original = turn_history.install_snapshot

    def interrupt(data_dir, source):
        if installed:
            original(data_dir, source)
        raise OSError("interrupted installation")

    with monkeypatch.context() as patch:
        patch.setattr(turn_history, "install_snapshot", interrupt)
        for _ in range(2):
            with pytest.raises(OSError, match="interrupted installation"):
                getattr(history, operation)()
            assert history.get_control_state()["restore_target"] is not None

    result = getattr(history, operation)()
    assert result["turn_id"] == expected_turn
    assert result["action"] == ("undone" if operation == "undo" else "redone")
    if operation == "undo":
        assert result["restored_pending_turn_ids"] == [expected_turn]
    with history._campaign_connect() as conn:
        assert [row[0] for row in conn.execute("SELECT turn_id FROM turn_confirmations")] == ["turn-1"]
    assert history.recover_interrupted() is False
    assert getattr(history, operation)()["turn_id"] != expected_turn
