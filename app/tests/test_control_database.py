import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.runtime.campaign_migration import ensure_campaign_database
from app.runtime.paths import CampaignPaths
from app.runtime.save_archive import SaveArchiveError, create_consistent_snapshot, export_save, extract_archive
from app.runtime.turn_history import CampaignHistory, ensure_control_database
from app.runtime.sqlite import connect_sqlite


def test_ensure_control_rejects_empty_control_db(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    (save / ".runtime").mkdir(parents=True)
    control = save / ".runtime" / "control.db"
    control.write_bytes(b"")
    paths = CampaignPaths.from_data_dir(save)
    with pytest.raises(RuntimeError, match="(quick_check|missing required control tables)"):
        ensure_control_database(paths)
    # fail-closed: no control schema may be auto-created over the empty file
    tables = {
        row[0]
        for row in sqlite3.connect(control).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "turn_transactions" not in tables
    assert "turn_snapshots" not in tables
    assert "control_state" not in tables


def test_ensure_control_rejects_control_db_missing_base_tables(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    (save / ".runtime").mkdir(parents=True)
    control = save / ".runtime" / "control.db"
    conn = sqlite3.connect(control)
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.commit()
    conn.close()
    paths = CampaignPaths.from_data_dir(save)
    with pytest.raises(RuntimeError, match="missing required control tables"):
        ensure_control_database(paths)
    assert sqlite3.connect(control).execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall() == [("unrelated",)]


def test_undo_does_not_restore_settings_but_restores_campaign(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    (save / "core_memory.json").write_text('{"items":[]}')
    (save / "initialization.json").write_text('{"phase":"completed"}')
    (save / "settings.json").write_text('{"val":1}')
    # create campaign with marker
    conn = connect_sqlite(save / "campaign.db")
    conn.execute("CREATE TABLE IF NOT EXISTS game_state (value TEXT)")
    conn.execute("INSERT INTO game_state VALUES ('before')")
    conn.commit()
    conn.close()
    hist = CampaignHistory(save)
    hist.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    hist.begin_confirmation("t1", write_db=True, scene_text=None)
    # modify after begin
    conn = connect_sqlite(save / "campaign.db")
    conn.execute("UPDATE game_state SET value='after'")
    conn.commit()
    conn.close()
    (save / "core_memory.json").write_text('{"items":[{"id":"1"}]}')
    with hist._campaign_connect() as campaign:
        campaign.execute("BEGIN")
        hist.record_confirmation(campaign, "t1", scene_text="场景正文")
    hist.complete_confirmation("t1", "g1")
    # change settings and core after
    (save / "settings.json").write_text('{"val":2}')
    (save / "core_memory.json").write_text('{"items":[{"id":"2"}]}')
    conn = connect_sqlite(save / "campaign.db")
    conn.execute("UPDATE game_state SET value='changed'")
    conn.commit()
    conn.close()
    hist.undo()
    # settings should stay 2
    assert json.loads((save / "settings.json").read_text())["val"] == 2
    # core should be restored to before (which was {"items":[]} captured at begin, not the "1" after begin)
    # Actually core captured at begin is {"items":[]} (before begin). After undo it should be that.
    assert json.loads((save / "core_memory.json").read_text()) == {"items": []}
    # campaign should be before
    conn = connect_sqlite(save / "campaign.db")
    val = conn.execute("SELECT value FROM game_state").fetchone()[0]
    conn.close()
    assert val == "before"


def test_export_contains_control_not_turn_history_and_snapshot_settings_excluded(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    from app.runtime.save_manifest import SaveManifestRepository

    SaveManifestRepository(save).create("Test")
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    hist = CampaignHistory(save)
    hist.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    hist.begin_confirmation("t1", write_db=True, scene_text=None)
    with hist._campaign_connect() as campaign:
        campaign.execute("BEGIN")
        hist.record_confirmation(campaign, "t1", scene_text="场景正文")
    hist.complete_confirmation("t1", "g1")
    (save / "settings.json").write_text('{"a":1}')
    archive = tmp_path / "export.zip"
    export_save(save, archive)
    import zipfile

    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        assert "save/.runtime/control.db" in names
        assert "save/.runtime/turn_history.db" not in names
        assert not any(".snapshots" in n and "settings.json" in n for n in names)
