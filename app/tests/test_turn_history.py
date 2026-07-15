import json
import sqlite3

from app.domain.core_memory import InitConfig, build_initial_current_state
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime, PendingOpening
from app.runtime.turn_history import CampaignHistory


def _write_value(path, value: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS game_state (value TEXT NOT NULL)")
        conn.execute("DELETE FROM game_state")
        conn.execute("INSERT INTO game_state VALUES (?)", (value,))


def _read_value(path) -> str:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT value FROM game_state").fetchone()[0]


def test_pending_opening_is_restored_after_runtime_restart(tmp_path):
    paths = CampaignPaths.from_data_dir(tmp_path / "save")
    config = InitConfig(
        world_name="泰瑞大陆",
        world_style="奇幻",
        ability_system="魔法",
        protagonist_name="莱尔",
    )
    pending = PendingOpening(
        turn_id="opening-1",
        config=config,
        current_state=build_initial_current_state(config),
        scene_result=SceneWriterResult(scene_text="莱尔在晨雾中醒来。"),
    )
    first = CampaignRuntime("restart-test", paths=paths)
    first.pending_openings.add(pending)
    first.close()

    second = CampaignRuntime("restart-test", paths=paths)
    try:
        restored = second.pending_openings.get("opening-1")
        assert restored is not None
        assert restored.config.world_name == "泰瑞大陆"
        assert restored.current_state.player.name == "莱尔"
        assert restored.scene_result.scene_text == "莱尔在晨雾中醒来。"
    finally:
        second.close()


def test_interrupted_confirmation_restores_pre_turn_database(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "game.db"
    _write_value(game_db, "before")
    history = CampaignHistory(data_dir)
    history.save_pending(
        turn_id="turn-1",
        kind="turn",
        group_id="group-1",
        turn_index=1,
        payload={"turn_id": "turn-1"},
    )
    history.begin_confirmation("turn-1", write_db=True, scene_text=None)
    _write_value(game_db, "partially-written")
    history.fail_confirmation("turn-1", RuntimeError("connection lost"))
    assert _read_value(game_db) == "before"

    restarted = CampaignHistory(data_dir)
    assert restarted.recover_interrupted() is True
    assert _read_value(game_db) == "before"
    assert restarted.pending_records()[0]["status"] == "awaiting_confirm"


def test_turn_journal_persists_incremental_state_audit(tmp_path):
    history = CampaignHistory(tmp_path / "save")
    history.save_pending(
        turn_id="turn-audit",
        kind="turn",
        group_id="turn-audit",
        turn_index=3,
        payload={"turn_id": "turn-audit"},
    )

    history.record_state_audit(
        "turn-audit",
        state_changes={"operations": [{"type": "money_delta", "delta": -2}]},
        state_before={"resources": {"money": 10}},
        state_after={"resources": {"money": 8}},
    )

    row = history.get_transaction("turn-audit")
    assert row is not None
    assert json.loads(row["state_changes_json"])["operations"][0]["delta"] == -2
    assert json.loads(row["state_before_json"])["resources"]["money"] == 10
    assert json.loads(row["state_after_json"])["resources"]["money"] == 8


def test_undo_and_redo_restore_confirmed_turn_snapshot(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "game.db"
    _write_value(game_db, "before")
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
    history.complete_confirmation("turn-1", "group-1")

    assert history.status()["can_undo"] is True
    assert history.undo()["turn_id"] == "turn-1"
    assert _read_value(game_db) == "before"
    assert history.status()["can_redo"] is True

    assert history.redo()["turn_id"] == "turn-1"
    assert _read_value(game_db) == "after"
    assert history.status()["can_redo"] is False


def test_only_ten_latest_confirmed_turn_snapshots_are_retained(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "game.db"
    _write_value(game_db, "0")
    history = CampaignHistory(data_dir, max_undo=10)

    for turn_index in range(1, 13):
        turn_id = f"turn-{turn_index}"
        history.save_pending(
            turn_id=turn_id,
            kind="turn",
            group_id=turn_id,
            turn_index=turn_index,
            payload={"turn_id": turn_id},
        )
        history.begin_confirmation(turn_id, write_db=True, scene_text=None)
        _write_value(game_db, str(turn_index))
        history.complete_confirmation(turn_id, turn_id)

    status = history.status()
    assert status["undo_count"] == 10
    assert {item["turn_index"] for item in status["snapshots"]} == set(range(3, 13))
    assert len(list((data_dir / ".snapshots").glob("undo-*"))) == 10
