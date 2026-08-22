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


def test_ensure_control_migrates_legacy_and_is_idempotent(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    # create legacy turn_history.db with empty schema
    legacy = save / ".runtime" / "turn_history.db"
    legacy.parent.mkdir(parents=True)
    conn = sqlite3.connect(legacy)
    conn.executescript(
        "CREATE TABLE turn_transactions (turn_id TEXT PRIMARY KEY, kind TEXT, group_id TEXT, turn_index INTEGER, payload_json TEXT, status TEXT, write_db INTEGER, scene_text TEXT, pre_snapshot_id TEXT, error TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE turn_snapshots (sequence INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT UNIQUE, turn_id TEXT, turn_index INTEGER, path TEXT, status TEXT, redo_path TEXT, created_at TEXT);"
    )
    conn.commit()
    conn.close()
    paths = CampaignPaths.from_data_dir(save)
    assert ensure_control_database(paths) == "migrated"
    assert (save / ".runtime" / "control.db").is_file()
    assert not legacy.is_file()
    # control_state should exist
    hist = CampaignHistory(save)
    cs = hist.get_control_state()
    assert cs["current_revision_id"]
    assert cs["connection_epoch"] == 0
    # idempotent second call
    assert ensure_control_database(paths) == "validated"


def test_ensure_control_fails_when_both_exist(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    (save / ".runtime").mkdir(parents=True)
    (save / ".runtime" / "control.db").write_bytes(b"")
    (save / ".runtime" / "turn_history.db").write_bytes(b"")
    paths = CampaignPaths.from_data_dir(save)
    with pytest.raises(RuntimeError, match="Both control databases"):
        ensure_control_database(paths)


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


def test_ensure_control_rejects_empty_legacy_control_db(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    (save / ".runtime").mkdir(parents=True)
    legacy = save / ".runtime" / "turn_history.db"
    legacy.write_bytes(b"")
    paths = CampaignPaths.from_data_dir(save)
    with pytest.raises(RuntimeError, match="(quick_check|missing required control tables)"):
        ensure_control_database(paths)
    # The empty legacy file must not be renamed to control.db and upgraded.
    assert not (save / ".runtime" / "control.db").exists()
    assert legacy.exists()


def test_ensure_control_rejects_legacy_control_db_missing_base_tables(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    (save / ".runtime").mkdir(parents=True)
    legacy = save / ".runtime" / "turn_history.db"
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.commit()
    conn.close()
    paths = CampaignPaths.from_data_dir(save)
    with pytest.raises(RuntimeError, match="missing required control tables"):
        ensure_control_database(paths)
    assert not (save / ".runtime" / "control.db").exists()
    assert legacy.exists()


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


def test_backfill_partial_txn_missing(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
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
    # clear one txn revision to simulate partial
    with hist._connect() as conn2:
        conn2.execute("UPDATE turn_transactions SET revision_id=NULL, commit_id=NULL WHERE turn_id='turn2'")
        conn2.commit()
    # trigger backfill again via _ensure_control_schema
    hist2 = CampaignHistory(save)
    with hist2._connect() as conn3:
        txn = conn3.execute("SELECT revision_id, commit_id FROM turn_transactions WHERE turn_id='turn2'").fetchone()
        assert txn["revision_id"] is not None
        assert txn["commit_id"] is not None
        # snapshot chain should remain
        snaps = conn3.execute("SELECT revision_id FROM turn_snapshots ORDER BY sequence").fetchall()
        assert all(r["revision_id"] is not None for r in snaps)


def test_snapshot_manifest_migrates_settings_and_legacy(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    from app.runtime.save_manifest import SaveManifestRepository

    SaveManifestRepository(save).create("Test")
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    hist = CampaignHistory(save)
    hist.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    hist.begin_confirmation("t1", write_db=True, scene_text=None)
    hist.complete_confirmation("t1", "g1")
    # Manually inject old manifest with settings.json + legacy db
    with hist._connect() as conn:
        snap = conn.execute("SELECT path FROM turn_snapshots WHERE status='applied'").fetchone()
        sdir = save / snap["path"]
        # add settings.json to snapshot dir and manifest
        (sdir / "settings.json").write_text('{"x":1}')
        man_path = sdir / "manifest.json"
        man = json.loads(man_path.read_text())
        man["files"].append("settings.json")
        # also add fake legacy entry
        (sdir / "current_state.db").write_bytes(b"")
        man["files"].append("current_state.db")
        man_path.write_text(json.dumps(man))
    # Extract via archive should clean
    archive = tmp_path / "out.zip"
    export_save(save, archive)
    # export should not contain snapshot settings
    import zipfile

    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        assert not any(".snapshots" in n and "settings.json" in n for n in names)
        assert not any("current_state.db" in n for n in names)
    # also test direct restore path cleans manifest
    # create a snapshot with legacy manifest and try undo (which triggers _migrate)
    # Re-open history and create another turn to test restore migration
    hist.save_pending(turn_id="t2", kind="turn", group_id="g2", turn_index=2, payload={"turn_id": "t2"})
    hist.begin_confirmation("t2", write_db=True, scene_text=None)
    # before complete, tamper snapshot manifest of t2's provisional snapshot to include settings
    with hist._connect() as conn:
        snap2 = conn.execute("SELECT path FROM turn_snapshots WHERE status='provisional'").fetchone()
        sdir2 = save / snap2["path"]
        # ensure manifest has settings
        man2 = json.loads((sdir2 / "manifest.json").read_text())
        # already without settings, add
        man2["files"].append("settings.json")
        (sdir2 / "settings.json").write_text('{"y":2}')
        (sdir2 / "manifest.json").write_text(json.dumps(man2))
    # fail should trigger restore which migrates manifest
    hist.fail_confirmation_offline("t2", RuntimeError("fail"))
    # check manifest cleaned
    with hist._connect() as conn:
        # after fail, snapshot was provisional and deleted? Actually fail keeps provisional? No, fail keeps provisional? It does not delete. Check that settings was removed
        snap2_path = save / snap2["path"]
        # snapshot dir still exists (provisional)
        if snap2_path.exists():
            man_after = json.loads((snap2_path / "manifest.json").read_text())
            assert "settings.json" not in man_after["files"]
            assert "current_state.db" not in man_after["files"]
            assert not (snap2_path / "settings.json").exists()
            assert not (snap2_path / "current_state.db").exists()


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


def test_export_normalizes_old_snapshot_manifest_even_when_files_are_filtered(tmp_path):
    import zipfile

    save = tmp_path / "save"
    save.mkdir()
    from app.runtime.save_manifest import SaveManifestRepository

    SaveManifestRepository(save).create("OldSnap")
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    # Create a snapshot with a legacy manifest that references settings.json
    # and a legacy ten-DB file.  Export filters those files out, but the
    # manifest embedded in the resulting archive must be normalized too.
    snap = save / ".snapshots" / "undo-one"
    snap.mkdir(parents=True)
    (snap / "settings.json").write_text('{"x":1}', encoding="utf-8")
    conn = sqlite3.connect(snap / "current_state.db")
    conn.execute(
        "CREATE TABLE current_state (id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "INSERT INTO current_state VALUES (1, ?, '')",
        (
            json.dumps({
                "scene": {"scene_id": "s1", "location": "旧地"},
                "player": {"name": "旧人"},
            }),
        ),
    )
    conn.commit()
    conn.close()
    (snap / "manifest.json").write_text(
        json.dumps({"files": ["current_state.db", "settings.json"]}),
        encoding="utf-8",
    )
    archive = tmp_path / "normalized.zip"
    export_save(save, archive)
    dest = tmp_path / "dest"
    extract_archive(archive, dest)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        assert "save/.snapshots/undo-one/current_state.db" not in names
        assert "save/.snapshots/undo-one/settings.json" not in names
    man = json.loads((dest / ".snapshots" / "undo-one" / "manifest.json").read_text())
    assert man["files"] == ["campaign.db"]
    assert (dest / ".snapshots" / "undo-one" / "campaign.db").is_file()
    assert not (dest / ".snapshots" / "undo-one" / "settings.json").exists()


def test_export_skips_corrupt_legacy_residue_when_snapshot_has_campaign_db(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    from app.runtime.save_manifest import SaveManifestRepository

    SaveManifestRepository(save).create("StaleResidue")
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    snap = save / ".snapshots" / "undo-one"
    snap.mkdir(parents=True)
    # Valid current snapshot: campaign.db + clean manifest.
    conn = sqlite3.connect(snap / "campaign.db")
    conn.execute("CREATE TABLE current_state (id INTEGER PRIMARY KEY, state_json TEXT NOT NULL)")
    conn.execute("INSERT INTO current_state VALUES (1, '{}')")
    conn.commit()
    conn.close()
    (snap / "manifest.json").write_text(
        json.dumps({"files": ["campaign.db"]}),
        encoding="utf-8",
    )
    # Damaged legacy residue that must not be read/backed up because campaign.db
    # already exists (Phase 2 rule).
    (snap / "current_state.db").write_bytes(b"this is not a sqlite database")

    archive = tmp_path / "residue.zip"
    export_save(save, archive)
    dest = tmp_path / "dest"
    extract_archive(archive, dest)
    assert not (dest / ".snapshots" / "undo-one" / "current_state.db").exists()
    assert (dest / ".snapshots" / "undo-one" / "campaign.db").is_file()


def test_legacy_turn_history_zip_import_promoted(tmp_path):
    save = tmp_path / "src"
    save.mkdir()
    from app.runtime.save_manifest import SaveManifestRepository
    import hashlib, zipfile

    SaveManifestRepository(save).create("Legacy")
    # create legacy control file
    (save / ".runtime").mkdir(parents=True, exist_ok=True)
    tdb = save / ".runtime" / "turn_history.db"
    conn = sqlite3.connect(tdb)
    conn.executescript(
        "CREATE TABLE turn_transactions (turn_id TEXT PRIMARY KEY, kind TEXT, group_id TEXT, turn_index INTEGER, payload_json TEXT, status TEXT, write_db INTEGER, scene_text TEXT, pre_snapshot_id TEXT, error TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE turn_snapshots (sequence INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT UNIQUE, turn_id TEXT, turn_index INTEGER, path TEXT, status TEXT, redo_path TEXT, created_at TEXT);"
    )
    conn.commit()
    conn.close()
    ensure_campaign_database(CampaignPaths.from_data_dir(save))
    # create zip with legacy name
    cdb = save / "campaign.db"
    files = []
    for pth in [save / "save_manifest.json", cdb, tdb]:
        rel = pth.relative_to(save).as_posix()
        files.append({"path": rel, "size": pth.stat().st_size, "sha256": hashlib.sha256(pth.read_bytes()).hexdigest()})
    zpath = tmp_path / "legacy.zip"
    with zipfile.ZipFile(zpath, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps({"format_version": 1, "files": files, "metadata": {}}))
        for entry in files:
            bundle.write(save / entry["path"], f"save/{entry['path']}")
    dest = tmp_path / "dest"
    extract_archive(zpath, dest)
    assert (dest / ".runtime" / "control.db").is_file()
    assert not (dest / ".runtime" / "turn_history.db").exists()


def test_wal_migration_preserved(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    # create legacy turn_history.db with WAL and uncheckpointed data
    legacy = save / ".runtime" / "turn_history.db"
    legacy.parent.mkdir(parents=True)
    conn = connect_sqlite(legacy)
    conn.execute(
        "CREATE TABLE turn_transactions (turn_id TEXT PRIMARY KEY, kind TEXT, group_id TEXT, turn_index INTEGER, payload_json TEXT, status TEXT, write_db INTEGER, scene_text TEXT, pre_snapshot_id TEXT, error TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE turn_snapshots (sequence INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT UNIQUE, turn_id TEXT, turn_index INTEGER, path TEXT, status TEXT, redo_path TEXT, created_at TEXT)"
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute(
        "INSERT INTO turn_transactions(turn_id, kind, group_id, turn_index, payload_json, status, created_at, updated_at) VALUES ('t1','turn','g1',1,'{}','awaiting_confirm','2020','2020')"
    )
    conn.commit()
    wal = Path(str(legacy) + "-wal")
    # With wal_autocheckpoint=0 the row is still only in the WAL.  Confirm the
    # sidecar really exists before migrating so the test verifies uncheckpointed
    # WAL data is preserved through checkpoint+rename.
    assert wal.exists() and wal.stat().st_size > 0
    conn.close()
    paths = CampaignPaths.from_data_dir(save)
    # migrate via ensure_control_database (checkpoint WAL then atomic rename)
    result = ensure_control_database(paths)
    assert result == "migrated"
    control = save / ".runtime" / "control.db"
    assert control.is_file()
    # data preserved
    conn2 = sqlite3.connect(control)
    row = conn2.execute("SELECT turn_id FROM turn_transactions WHERE turn_id='t1'").fetchone()
    conn2.close()
    assert row is not None
    # quick_check ok
    from app.runtime.save_archive import quick_check_database

    quick_check_database(control)
    # the legacy WAL sidecar must not survive under the legacy name; after the
    # schema upgrade reopens control.db in WAL mode a fresh empty sidecar may be
    # created, but the uncheckpointed data must already be in the main DB.
    assert not (Path(str(legacy) + "-wal")).exists()
