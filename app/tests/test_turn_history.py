import json
import sqlite3
from contextlib import closing

import pytest

from app.domain.core_memory import InitConfig, build_initial_current_state
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime, PendingOpening
from app.runtime.sqlite import connect_sqlite
from app.runtime.turn_history import CampaignHistory


def _write_value(path, value: str) -> None:
    # Ensure canonical campaign schema exists so snapshots pass strict validation;
    # game_state is an extra test table on top of the real schema.
    from pathlib import Path as _Path
    from app.runtime.campaign_migration import ensure_campaign_database
    from app.runtime.paths import CampaignPaths as _CP

    _p = _Path(path)
    try:
        ensure_campaign_database(_CP.from_data_dir(_p.parent))
    except Exception:
        pass
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








def test_failed_confirmation_restores_wal_database_with_live_connection(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    from app.runtime.campaign_migration import ensure_campaign_database
    from app.runtime.paths import CampaignPaths
    ensure_campaign_database(CampaignPaths.from_data_dir(data_dir))
    game_db = data_dir / "campaign.db"
    connection = connect_sqlite(game_db)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS game_state (value TEXT NOT NULL)")
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
        # Phase 4 offline restore requires campaign connections to be closed.
        # The live connection becomes stale after os.replace; close it before restore.
        connection.close()
        connection = None  # type: ignore[assignment]

        history.fail_confirmation_offline("turn-wal", RuntimeError("failed"))

        assert _read_value(game_db) == "before"
    finally:
        if connection is not None:
            connection.close()

    assert _read_value(game_db) == "before"




def test_undo_and_redo_restore_confirmed_turn_snapshot(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "campaign.db"
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










def test_turn_transaction_window_keeps_multiple_rows_per_turn(tmp_path):
    data_dir = tmp_path / "save"
    data_dir.mkdir()
    game_db = data_dir / "campaign.db"
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




def _all_transactions(history) -> list[dict]:
    with history._connect() as conn:
        rows = conn.execute(
            "SELECT turn_id, turn_index, status FROM turn_transactions"
        ).fetchall()
    return [dict(row) for row in rows]
