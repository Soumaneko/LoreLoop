import json
import sqlite3

import pytest

from app.domain.core_memory import InitConfig, build_initial_current_state
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime, PendingOpening
from app.runtime.sqlite import connect_sqlite
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


def test_pending_opening_requires_the_current_storage_contract(tmp_path):
    paths = CampaignPaths.from_data_dir(tmp_path / "save")
    history = CampaignHistory(paths.data_dir)
    history.save_pending(
        turn_id="opening-invalid",
        kind="opening",
        group_id=None,
        turn_index=0,
        payload={
            "turn_id": "opening-invalid",
            "config": {},
            "current_state": {},
            "scene_result": {},
            "original_scene_text": None,
        },
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"current storage contract; missing fields: "
            r"experience_mode, group_id, initialization_fingerprint, version_index"
        ),
    ):
        CampaignRuntime("invalid-pending-opening", paths=paths)


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


def test_failed_opening_confirmation_restores_initialization_and_absent_state(
    tmp_path,
):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    initialization = data_dir / "initialization.json"
    initialization.write_text(
        '{"phase":"opening_pending"}',
        encoding="utf-8",
    )
    history = CampaignHistory(data_dir)
    history.save_pending(
        turn_id="opening-1",
        kind="opening",
        group_id="opening-1",
        turn_index=0,
        payload={"turn_id": "opening-1"},
    )
    history.begin_confirmation(
        "opening-1",
        write_db=True,
        scene_text=None,
    )

    _write_value(data_dir / "current_state.db", "partially-created")
    initialization.write_text('{"phase":"completed"}', encoding="utf-8")
    history.fail_confirmation("opening-1", RuntimeError("confirmation failed"))

    assert not (data_dir / "current_state.db").exists()
    assert initialization.read_text(encoding="utf-8") == (
        '{"phase":"opening_pending"}'
    )


def test_failed_confirmation_restores_wal_database_with_live_connection(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "game.db"
    connection = connect_sqlite(game_db)
    try:
        connection.execute("CREATE TABLE game_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO game_state VALUES ('before')")
        connection.commit()

        history = CampaignHistory(data_dir)
        history.save_pending(
            turn_id="turn-wal",
            kind="turn",
            group_id="group-wal",
            turn_index=1,
            payload={"turn_id": "turn-wal"},
        )
        history.begin_confirmation("turn-wal", write_db=True, scene_text=None)
        connection.execute("UPDATE game_state SET value='partially-written'")
        connection.commit()

        history.fail_confirmation("turn-wal", RuntimeError("failed"))

        assert connection.execute(
            "SELECT value FROM game_state"
        ).fetchone()[0] == "before"
    finally:
        connection.close()

    assert _read_value(game_db) == "before"


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
        state_warnings=[{
            "operation_id": "op2",
            "code": "npc_target_not_present",
        }],
        state_before={"resources": {"money": 10}},
        state_after={"resources": {"money": 8}},
    )

    row = history.get_transaction("turn-audit")
    assert row is not None
    assert json.loads(row["state_changes_json"])["operations"][0]["delta"] == -2
    assert json.loads(row["state_warnings_json"])[0]["operation_id"] == "op2"
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
    undo_result = history.undo()
    assert undo_result["turn_id"] == "turn-1"
    assert undo_result["restored_pending_turn_ids"] == ["turn-1"]
    assert _read_value(game_db) == "before"
    assert history.status()["can_redo"] is True
    assert [row["turn_id"] for row in history.pending_records()] == ["turn-1"]

    assert history.redo()["turn_id"] == "turn-1"
    assert _read_value(game_db) == "after"
    assert history.status()["can_redo"] is False
    assert history.pending_records() == []


def test_undo_restores_all_retry_variants_to_awaiting_confirmation(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "game.db"
    _write_value(game_db, "before")
    history = CampaignHistory(data_dir)
    for turn_id, version in (("turn-v1", 1), ("turn-v2", 2)):
        history.save_pending(
            turn_id=turn_id,
            kind="turn",
            group_id="group-1",
            turn_index=1,
            payload={"turn_id": turn_id, "version_index": version},
        )

    history.begin_confirmation(
        "turn-v2",
        write_db=True,
        scene_text="确认前编辑后的正文。",
    )
    _write_value(game_db, "after")
    history.complete_confirmation("turn-v2", "group-1")

    result = history.undo()

    assert result["restored_pending_turn_ids"] == ["turn-v1", "turn-v2"]
    records = history.pending_records()
    assert [record["turn_id"] for record in records] == ["turn-v1", "turn-v2"]
    assert next(
        record for record in records if record["turn_id"] == "turn-v2"
    )["scene_text"] == "确认前编辑后的正文。"
    assert _read_value(game_db) == "before"


def test_runtime_undo_restores_edited_opening_as_pending_and_redo_clears_it(tmp_path):
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
        scene_result=SceneWriterResult(scene_text="最初生成的开场。"),
    )
    runtime = CampaignRuntime("undo-opening", paths=paths, max_workers=1)
    try:
        runtime.pending_openings.add(pending)
        runtime.begin_confirmation(
            pending,
            write_db=True,
            scene_text="确认前编辑后的开场。",
        )
        runtime.pending_openings.remove(pending.turn_id)
        runtime.complete_confirmation(pending)

        result = runtime.undo()

        assert result["restored_pending_turn_ids"] == ["opening-1"]
        restored = runtime.pending_openings.get("opening-1")
        assert restored is not None
        assert restored.scene_result.scene_text == "确认前编辑后的开场。"

        runtime.redo()
        assert runtime.pending_openings.get("opening-1") is None
    finally:
        runtime.close()


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


def test_turn_transactions_retained_for_same_window_as_undo_snapshots(tmp_path):
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

    remaining = {
        row["turn_id"] for row in history.pending_records()
    } | {row["turn_id"] for row in _all_transactions(history)}
    assert remaining == {f"turn-{index}" for index in range(3, 13)}
    assert history.get_transaction("turn-1") is None
    assert history.get_transaction("turn-3") is not None


def test_turn_transaction_window_keeps_multiple_rows_per_turn(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "game.db"
    _write_value(game_db, "0")
    history = CampaignHistory(data_dir, max_undo=10)

    for turn_index in range(1, 13):
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
        history.complete_confirmation(versions[1], group_id)

    turn_ids = {row["turn_id"] for row in _all_transactions(history)}
    assert {f"group-11-v{version}" for version in (1, 2)} <= turn_ids
    assert {f"group-12-v{version}" for version in (1, 2)} <= turn_ids
    assert not (turn_ids & {f"group-1-v{version}" for version in (1, 2)})
    assert history.get_transaction("group-10-v1") is not None


def test_transaction_trim_follows_max_undo_window(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "game.db"
    _write_value(game_db, "0")
    history = CampaignHistory(data_dir, max_undo=3)

    for turn_index in range(1, 6):
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

    turn_ids = {row["turn_id"] for row in _all_transactions(history)}
    assert turn_ids == {f"turn-{index}" for index in range(3, 6)}
    assert history.get_transaction("turn-2") is None


def _all_transactions(history) -> list[dict]:
    with history._connect() as conn:
        rows = conn.execute(
            "SELECT turn_id, turn_index, status FROM turn_transactions"
        ).fetchall()
    return [dict(row) for row in rows]
