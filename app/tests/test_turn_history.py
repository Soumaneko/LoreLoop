import sqlite3
from contextlib import closing
from pathlib import Path
import pytest
from app.domain.core_memory import InitConfig, build_initial_current_state
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.campaign_migration import ensure_campaign_database
from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime, PendingOpening
from app.runtime.turn_history import CampaignHistory


def _write_value(path, value: str) -> None:
    ensure_campaign_database(CampaignPaths.from_data_dir(Path(path).parent))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS game_state (value TEXT NOT NULL)")
        conn.execute("DELETE FROM game_state")
        conn.execute("INSERT INTO game_state VALUES (?)", (value,))
        conn.commit()


def _read_value(path) -> str:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT value FROM game_state").fetchone()[0]


def test_pending_opening_is_restored_after_runtime_restart(tmp_path):
    paths = CampaignPaths.from_data_dir(tmp_path / "save")
    config = InitConfig(
        world_name="泰瑞大陆",
        world_style="奇幻",
        ability_system="魔法",
        protagonist_name="莱尔",
        story_expectation="探索世界。",
    )
    pending = PendingOpening(
        turn_id="opening-1",
        group_id="opening-group-1",
        version_index=1,
        initialization_fingerprint="fingerprint-1",
        config=config,
        experience_mode="player",
        current_state=build_initial_current_state(
            config,
            location="泰瑞大陆",
            currency_unit="金币",
        ),
        scene_result=SceneWriterResult(scene_text="莱尔在晨雾中醒来。"),
        token_usage={
            "generation": {
                "input_tokens": 700,
                "output_tokens": 300,
                "total_tokens": 1000,
            },
            "confirmation": None,
        },
    )
    first = CampaignRuntime("restart-test", paths=paths)
    first.pending_openings.add(pending)
    first.close()

    second = CampaignRuntime("restart-test", paths=paths)
    try:
        restored = second.pending_openings.get("opening-1")
        assert restored is not None
        assert restored.group_id == "opening-group-1"
        assert restored.version_index == 1
        assert restored.experience_mode == "player"
        assert restored.config.world_name == "泰瑞大陆"
        assert restored.current_state.player.name == "莱尔"
        assert restored.scene_result.scene_text == "莱尔在晨雾中醒来。"
        assert restored.token_usage == pending.token_usage
    finally:
        second.close()


def test_undo_and_redo_restore_confirmed_turn_snapshot(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "campaign.db"
    _write_value(game_db, "before")
    settings = data_dir / "settings.json"
    core = data_dir / "core_memory.json"
    core.write_text('{"items":[]}', encoding="utf-8")
    notes = data_dir / "notes.db"
    notes.write_bytes(b"owner data")
    history = CampaignHistory(data_dir)
    history.save_pending(
        turn_id="turn-1",
        kind="turn",
        group_id="group-1",
        turn_index=1,
        payload={"turn_id": "turn-1"},
    )
    history.begin_confirmation("turn-1", write_db=True, scene_text=None)
    _write_value(game_db, "after")
    with history._campaign_connect() as campaign:
        campaign.execute("BEGIN")
        history.record_confirmation(campaign, "turn-1", scene_text="场景正文")
    history.complete_confirmation("turn-1", "group-1")

    settings.write_text('{"dice_display":"off"}', encoding="utf-8")
    core.write_text('{"items":[{"id":"after"}]}', encoding="utf-8")
    assert history.status()["can_undo"] is True
    undo_result = history.undo()
    assert undo_result["turn_id"] == "turn-1"
    assert undo_result["restored_pending_turn_ids"] == ["turn-1"]
    assert _read_value(game_db) == "before"
    assert core.read_text() == '{"items":[]}'
    assert settings.read_text() == '{"dice_display":"off"}'
    assert notes.read_bytes() == b"owner data"
    assert history.status()["can_redo"] is True
    assert [row["turn_id"] for row in history.pending_records()] == ["turn-1"]

    assert history.redo()["turn_id"] == "turn-1"
    assert _read_value(game_db) == "after"
    assert core.read_text() == '{"items":[{"id":"after"}]}'
    assert settings.read_text() == '{"dice_display":"off"}'
    assert notes.read_bytes() == b"owner data"
    assert history.status()["can_redo"] is False
    assert history.pending_records() == []


def test_turn_transaction_window_keeps_multiple_rows_per_turn(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "campaign.db"
    _write_value(game_db, "0")
    history = CampaignHistory(data_dir, max_undo=3)

    for turn_index in range(1, 6):
        group_id = f"group-{turn_index}"
        versions = [f"{group_id}-v{version}" for version in (1, 2)]
        for turn_id in versions:
            history.save_pending(
                turn_id=turn_id,
                kind="turn",
                group_id=group_id,
                turn_index=turn_index,
                payload={"turn_id": turn_id},
            )
        history.begin_confirmation(
            versions[1],
            write_db=True,
            scene_text=None,
        )
        _write_value(game_db, str(turn_index))
        with history._campaign_connect() as campaign:
            campaign.execute("BEGIN")
            history.record_confirmation(campaign, versions[1], scene_text="场景正文")
        history.complete_confirmation(versions[1], group_id)

    assert history.get_transaction("group-1-v2")["status"] == "completed"

    history.save_pending(turn_id="future", kind="turn", group_id="future", turn_index=6, payload={})
    history.discard(["future"])
    for turn_index in (5, 4):
        restored = history.undo()["restored_pending_turn_ids"]
        assert set(restored) == {f"group-{turn_index}-v1", f"group-{turn_index}-v2"}
        assert _read_value(game_db) == str(turn_index - 1)
        history.discard(restored)
    history.save_pending(turn_id="branch", kind="turn", group_id="branch", turn_index=4, payload={})
    history.begin_confirmation("branch", write_db=True, scene_text=None)
    with history._campaign_connect() as conn:
        conn.execute("BEGIN")
        history.record_confirmation(conn, "branch", scene_text="新分支")
    history.complete_confirmation("branch", "branch")
    history.discard(history.undo()["restored_pending_turn_ids"])
    assert set(history.undo()["restored_pending_turn_ids"]) == {"group-3-v1", "group-3-v2"}
    with pytest.raises(ValueError, match="No confirmed turn"):
        history.undo()


def test_completed_confirmation_survives_later_failure_and_result_lookup(tmp_path, monkeypatch):
    from app.runtime.player_state import load_confirmation_outcome
    from app.runtime import player_state

    paths = CampaignPaths.from_data_dir(tmp_path / "save")
    _write_value(paths.campaign_db, "before")
    runtime = CampaignRuntime("result-save", paths=paths)
    try:
        history = runtime.history
        history.save_pending(turn_id="variant-1", kind="turn", group_id="group-1",
                             turn_index=1, payload={})
        monkeypatch.setattr(player_state, "get_campaign_runtime", lambda: runtime)
        def outcome():
            return load_confirmation_outcome("result-save", "variant-1")["outcome"]
        assert outcome() == "not_confirmed"
        history.begin_confirmation("variant-1", write_db=True, scene_text=None)
        assert outcome() == "in_progress"
        _write_value(paths.campaign_db, "after")
        with history._campaign_connect() as campaign:
            campaign.execute("BEGIN")
            history.record_confirmation(campaign, "variant-1", scene_text="场景正文")
        history.complete_confirmation("variant-1", "group-1")
        runtime.fail_confirmation("variant-1", RuntimeError("presentation failed"))
        history.fail_confirmation("variant-1", RuntimeError("late error"))
        assert outcome() == "confirmed"
        with monkeypatch.context() as lookup_patch:
            def unreadable_control():
                raise OSError("control read failed")
            lookup_patch.setattr(history, "_connect", unreadable_control)
            assert outcome() == "confirmed"
        assert _read_value(paths.campaign_db) == "after"
        assert load_confirmation_outcome("other-save", "variant-1")["outcome"] == "unknown"
        assert load_confirmation_outcome("result-save", "other-variant")["outcome"] == "unknown"
        history.undo()
        assert outcome() == "not_confirmed"
        assert _read_value(paths.campaign_db) == "before"
    finally:
        runtime.close()


def test_confirmation_publication_preserves_redo_until_control_commit(tmp_path, monkeypatch):
    history = CampaignHistory(tmp_path / "save")
    for index in (1, 2):
        turn_id = f"turn-{index}"
        history.save_pending(turn_id=turn_id, kind="turn", group_id=turn_id,
                             turn_index=index, payload={})
        history.begin_confirmation(turn_id, write_db=True, scene_text=None)
        with history._campaign_connect() as conn:
            conn.execute("BEGIN")
            history.record_confirmation(conn, turn_id, scene_text=turn_id)
        history.complete_confirmation(turn_id, turn_id)
    history.undo()
    # Repeating already published cleanup must preserve the future redo branch.
    history.complete_confirmation("turn-1", "turn-1")
    assert history.status()["can_redo"] is True
    with history._connect() as conn:
        old_paths = [history._snapshot_path(row[0]) for row in conn.execute(
            "SELECT redo_path FROM turn_snapshots WHERE status='undone'")]
    history.save_pending(turn_id="new", kind="turn", group_id="new", turn_index=2, payload={})
    history.begin_confirmation("new", write_db=True, scene_text=None)
    with history._campaign_connect() as conn:
        conn.execute("BEGIN")
        history.record_confirmation(conn, "new", scene_text="new")
    original = history._trim_transactions_locked

    def fail_cleanup(_conn):
        raise OSError("control write failed")

    monkeypatch.setattr(history, "_trim_transactions_locked", fail_cleanup)
    with pytest.raises(OSError, match="control write failed"):
        history.complete_confirmation("new", "new")
    assert all(path.is_dir() for path in old_paths)
    monkeypatch.setattr(history, "_trim_transactions_locked", original)
    history.complete_confirmation("new", "new")
    assert history.status()["can_redo"] is False
    history.undo()
    history.redo()
    assert history.get_transaction("new")["status"] == "completed"


def test_recovery_publishes_reconfirmed_version_before_reconciling_old_snapshot(tmp_path):
    history = CampaignHistory(tmp_path / "save")
    game_db = history.data_dir / "campaign.db"
    _write_value(game_db, "before")
    history.save_pending(turn_id="same", kind="turn", group_id="same", turn_index=1, payload={})
    history.begin_confirmation("same", write_db=True, scene_text=None)
    _write_value(game_db, "first")
    with history._campaign_connect() as conn:
        conn.execute("BEGIN")
        history.record_confirmation(conn, "same", scene_text="first")
    history.complete_confirmation("same", "same")
    history.undo()
    history.begin_confirmation("same", write_db=True, scene_text=None)
    _write_value(game_db, "second")
    with history._campaign_connect() as conn:
        conn.execute("BEGIN")
        history.record_confirmation(conn, "same", scene_text="second")

    reopened = CampaignHistory(history.data_dir)
    assert reopened.recover_interrupted() is True
    with reopened._connect() as conn:
        assert [r[0] for r in conn.execute("SELECT status FROM turn_snapshots")] == ["applied"]
    assert reopened.recover_interrupted() is False
    assert reopened.undo()["turn_id"] == "same"
    assert _read_value(game_db) == "before"
    reopened.redo()
    assert _read_value(game_db) == "second"


def test_reopen_finishes_database_and_json_restore_together(tmp_path, monkeypatch):
    import json
    import shutil
    from app.runtime import turn_history

    paths = CampaignPaths.from_data_dir(tmp_path / "save")
    ensure_campaign_database(paths)
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
def test_interrupted_history_retry_only_completes_original_turn(tmp_path, monkeypatch, operation):
    from app.runtime import turn_history

    history = CampaignHistory(tmp_path / "save")
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
