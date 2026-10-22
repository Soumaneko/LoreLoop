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


def test_backfill_full_chain(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    # create old control.db without revision columns, 2 applied
    db = save / ".runtime" / "control.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE turn_transactions (turn_id TEXT PRIMARY KEY, kind TEXT, group_id TEXT, turn_index INTEGER, payload_json TEXT, status TEXT, write_db INTEGER, scene_text TEXT, pre_snapshot_id TEXT, error TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE turn_snapshots (sequence INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT UNIQUE, turn_id TEXT, turn_index INTEGER, path TEXT, status TEXT, redo_path TEXT, created_at TEXT);"
    )
    for i in [1, 2]:
        sid = f"snap{i}"
        tid = f"turn{i}"
        path = f".snapshots/undo-{sid}"
        (save / path).mkdir(parents=True)
        (save / path / "campaign.db").write_bytes(b"")
        (save / path / "manifest.json").write_text(json.dumps({"files": ["campaign.db"]}))
        conn.execute(
            "INSERT INTO turn_snapshots(snapshot_id, turn_id, turn_index, path, status, created_at) VALUES (?,?,?,?,?,?)",
            (sid, tid, i, path, "applied", "2020"),
        )
        conn.execute(
            "INSERT INTO turn_transactions(turn_id, kind, group_id, turn_index, payload_json, status, created_at, updated_at, pre_snapshot_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (tid, "turn", tid, i, "{}", "completed", "2020", "2020", sid),
        )
    conn.commit()
    conn.close()
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    hist = CampaignHistory(save)
    with hist._connect() as conn2:
        rows = conn2.execute("SELECT snapshot_id, revision_id FROM turn_snapshots ORDER BY sequence").fetchall()
        for r in rows:
            assert r["revision_id"] is not None
        txns = conn2.execute("SELECT turn_id, revision_id, commit_id FROM turn_transactions ORDER BY turn_index").fetchall()
        for r in txns:
            assert r["revision_id"] is not None
            assert r["commit_id"] is not None
        # chain continuity: snapshot pre == previous txn post would be implied by construction, but at least current should be last txn post
        cs = conn2.execute("SELECT current_revision_id FROM control_state").fetchone()
        assert cs["current_revision_id"] == txns[1]["revision_id"]


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


def test_undo_redo_epoch_and_revision(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    hist = CampaignHistory(save)
    hist.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    hist.begin_confirmation("t1", write_db=True, scene_text=None)
    hist.complete_confirmation("t1", "g1")
    rev_after = hist.get_control_state()["current_revision_id"]
    epoch0 = hist.get_control_state()["connection_epoch"]
    hist.save_pending(turn_id="t2", kind="turn", group_id="g2", turn_index=2, payload={"turn_id": "t2"})
    hist.begin_confirmation("t2", write_db=True, scene_text=None)
    hist.complete_confirmation("t2", "g2")
    rev2 = hist.get_control_state()["current_revision_id"]
    assert rev2 != rev_after
    hist.undo()
    cur = hist.get_control_state()
    assert cur["connection_epoch"] == epoch0 + 1
    # current should be pre of t2 (which equals rev_after)
    assert cur["current_revision_id"] == rev_after
    hist.redo()
    cur2 = hist.get_control_state()
    assert cur2["connection_epoch"] == epoch0 + 2
    assert cur2["current_revision_id"] == rev2


def test_export_contains_control_not_turn_history_and_snapshot_settings_excluded(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    from app.runtime.save_manifest import SaveManifestRepository

    SaveManifestRepository(save).create("Test")
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    hist = CampaignHistory(save)
    hist.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    hist.begin_confirmation("t1", write_db=True, scene_text=None)
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
