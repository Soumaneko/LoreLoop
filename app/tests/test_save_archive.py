from app.runtime.stores import open_director_log_store
import hashlib
import json
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest

from app.runtime.save_archive import (
    ArchiveLimits,
    SaveArchiveError,
    _safe_members,
    create_consistent_snapshot,
    export_save,
    extract_archive,
    inspect_archive,
    rebase_history_paths,
)
from app.runtime.save_manifest import SaveManifestRepository


def _make_database(path: Path, value: str = "saved") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE values_for_test (value TEXT)")
        connection.execute("INSERT INTO values_for_test VALUES (?)", (value,))
        connection.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL)"
        )
        connection.commit()


def _make_campaign_database(path: Path, value: str = "saved") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_versions "
            "(component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
        )
        for component, version in [
            ("current_state", 4),
            ("continuity", 3),
            ("short_term_memory", 5),
            ("scene_summary", 1),
            ("confirmed_history", 1),
            ("npc_profile", 9),
            ("director_event", 11),
            ("director_log", 1),
            ("director_npc", 5),
            ("world_setting", 12),
        ]:
            connection.execute(
                "INSERT INTO schema_versions(component, version) VALUES (?, ?)",
                (component, version),
            )
        connection.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT '')"
        )
        for table in [
            "continuity_items (id TEXT PRIMARY KEY)",
            "continuity_logs (id TEXT PRIMARY KEY)",
            "continuity_log_evidence (log_id TEXT, position INTEGER)",
            "continuity_processed_turns (turn_index INTEGER PRIMARY KEY)",
            "short_term_scenes (id INTEGER PRIMARY KEY)",
            "scene_summaries (id INTEGER PRIMARY KEY)",
            "scene_summary_meta (id INTEGER PRIMARY KEY)",
            "history_turns (turn_index INTEGER PRIMARY KEY)",
            "history_documents (document_id TEXT PRIMARY KEY)",
            "npc_profiles (npc_id TEXT PRIMARY KEY)",
            "npc_field_change_logs (log_id INTEGER PRIMARY KEY)",
            "npc_field_change_evidence (log_id INTEGER, position INTEGER)",
            "npc_field_change_processed_turns (turn_index INTEGER PRIMARY KEY)",
            "director_events (event_id TEXT PRIMARY KEY)",
            "director_npcs (draft_id TEXT PRIMARY KEY)",
            "director_log (id INTEGER PRIMARY KEY)",
            "world_settings (setting_id TEXT PRIMARY KEY)",
        ]:
            connection.execute(f"CREATE TABLE {table}")
        connection.execute("CREATE TABLE values_for_test (value TEXT)")
        connection.execute("INSERT INTO values_for_test VALUES (?)", (value,))
        connection.commit()


def _manifest_for(path: str, content: bytes) -> dict:
    return {
        "format_version": 1,
        "files": [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
        "metadata": {},
    }


def test_snapshot_uses_sqlite_backup_and_excludes_wal_and_shm(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    database = source / "campaign.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE values_for_test (value TEXT)")
        connection.execute("INSERT INTO values_for_test VALUES ('from-wal')")
        connection.commit()
        assert Path(f"{database}-wal").exists()

        destination = tmp_path / "snapshot"
        files = create_consistent_snapshot(source, destination)
        assert files == ["campaign.db"]
        assert not list(destination.glob("*-wal"))
        assert not list(destination.glob("*-shm"))
        with sqlite3.connect(destination / "campaign.db") as copied:
            assert copied.execute("SELECT value FROM values_for_test").fetchone()[0] == "from-wal"
    finally:
        connection.close()


def test_export_and_extract_round_trip_allowed_save_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("Alpha")
    _make_campaign_database(source / "campaign.db")
    journal = source / ".runtime" / "director_mutation.json"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"version": 1}', encoding="utf-8")
    _make_database(source / ".snapshots" / "undo-one" / "campaign.db", "old")
    (source / ".snapshots" / "undo-one" / "manifest.json").write_text(
        '{"files": ["campaign.db"]}', encoding="utf-8"
    )
    (source / "ignored.txt").write_text("not portable", encoding="utf-8")

    archive = tmp_path / "save.zip"
    exported = export_save(source, archive, metadata={"display_name": "Alpha"})
    assert exported.metadata == {"display_name": "Alpha"}
    assert "ignored.txt" not in exported.files
    with zipfile.ZipFile(archive) as bundle:
        assert "manifest.json" in bundle.namelist()
        assert "save/campaign.db" in bundle.namelist()
        assert "save/.runtime/director_mutation.json" in bundle.namelist()
        assert "save/ignored.txt" not in bundle.namelist()

    inspected = inspect_archive(archive)
    destination = tmp_path / "imported"
    extracted = extract_archive(archive, destination)
    assert extracted == inspected
    assert SaveManifestRepository(destination).load().display_name == "Alpha"
    assert (destination / ".runtime" / "director_mutation.json").is_file()
    with sqlite3.connect(destination / ".snapshots" / "undo-one" / "campaign.db") as db:
        assert db.execute("SELECT value FROM values_for_test").fetchone()[0] == "old"


def test_export_accepts_director_log_schema(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("Beta")
    with open_director_log_store(str(source / "campaign.db")) as store:
        store.save_entries([{
            "turn_index": 1,
            "phase": "generation",
            "reasoning": "r",
            "measures": "m",
        }])

    exported = export_save(source, tmp_path / "save.zip")

    assert "campaign.db" in exported.files


def test_snapshot_preserves_portable_turn_history_paths(tmp_path):
    source = tmp_path / "old-save"
    snapshots = source / ".snapshots"
    snapshots.mkdir(parents=True)
    undo = snapshots / "undo-one"
    redo = snapshots / "redo-one"
    undo.mkdir()
    redo.mkdir()
    (undo / "manifest.json").write_text('{"files": []}', encoding="utf-8")
    (redo / "manifest.json").write_text('{"files": []}', encoding="utf-8")
    history = source / ".runtime" / "control.db"
    history.parent.mkdir()
    with sqlite3.connect(history) as connection:
        connection.execute(
            "CREATE TABLE turn_snapshots (sequence INTEGER, path TEXT, redo_path TEXT)"
        )
        connection.execute(
            "INSERT INTO turn_snapshots VALUES (1, ?, ?)",
            (".snapshots/undo-one", ".snapshots/redo-one"),
        )

    copied = tmp_path / "copy"
    create_consistent_snapshot(source, copied)
    with sqlite3.connect(copied / ".runtime" / "control.db") as connection:
        path, redo_path = connection.execute(
            "SELECT path, redo_path FROM turn_snapshots"
        ).fetchone()
    assert path == ".snapshots/undo-one"
    assert redo_path == ".snapshots/redo-one"


def test_rebase_rejects_absolute_history_path(tmp_path):
    data_dir = tmp_path / "save"
    history = data_dir / ".runtime" / "control.db"
    history.parent.mkdir(parents=True)
    with sqlite3.connect(history) as connection:
        connection.execute(
            "CREATE TABLE turn_snapshots (sequence INTEGER, path TEXT, redo_path TEXT)"
        )
        connection.execute(
            "INSERT INTO turn_snapshots VALUES (1, ?, NULL)",
            (str(tmp_path / "somewhere-else" / ".snapshots" / "undo"),),
        )
    with pytest.raises(SaveArchiveError, match="must be relative"):
        rebase_history_paths(data_dir)


def test_import_rebase_rejects_absolute_snapshot_path(tmp_path):
    data_dir = tmp_path / "imported"
    snapshot = data_dir / ".snapshots" / "undo-one"
    snapshot.mkdir(parents=True)
    history = data_dir / ".runtime" / "control.db"
    history.parent.mkdir()
    with sqlite3.connect(history) as connection:
        connection.execute(
            "CREATE TABLE turn_snapshots (sequence INTEGER, path TEXT, redo_path TEXT)"
        )
        connection.execute(
            "INSERT INTO turn_snapshots VALUES (1, ?, NULL)",
            ("/old/computer/save/.snapshots/undo-one",),
        )
    with pytest.raises(SaveArchiveError, match="must be relative"):
        rebase_history_paths(data_dir)


def test_import_rebase_rejects_windows_absolute_snapshot_path(tmp_path):
    data_dir = tmp_path / "imported"
    snapshot = data_dir / ".snapshots" / "undo-one"
    snapshot.mkdir(parents=True)
    history = data_dir / ".runtime" / "control.db"
    history.parent.mkdir()
    with sqlite3.connect(history) as connection:
        connection.execute(
            "CREATE TABLE turn_snapshots (sequence INTEGER, path TEXT, redo_path TEXT)"
        )
        connection.execute(
            "INSERT INTO turn_snapshots VALUES (1, ?, NULL)",
            (r"C:\old\save\.snapshots\undo-one",),
        )
    with pytest.raises(SaveArchiveError, match="must be relative"):
        rebase_history_paths(data_dir)


@pytest.mark.parametrize("member", ["../escape.db", "/absolute.db"])
def test_inspection_rejects_zip_slip_paths(tmp_path, member):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr(member, b"bad")
    with pytest.raises(SaveArchiveError, match="Unsafe archive path"):
        inspect_archive(archive)


def test_member_validation_rejects_raw_backslash_path():
    info = zipfile.ZipInfo("save/bad.db")
    # ZipFile.writestr normalizes backslashes on Windows before writing them.
    # Set the fields directly to exercise validation of a raw non-canonical name.
    info.filename = r"save\bad.db"
    info.orig_filename = r"save\bad.db"

    class _Bundle:
        @staticmethod
        def infolist():
            return [info]

    with pytest.raises(SaveArchiveError, match="Unsafe archive path"):
        _safe_members(_Bundle(), ArchiveLimits())


def test_inspection_rejects_duplicate_members(tmp_path):
    archive = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("manifest.json", "{}")
            bundle.writestr("save/state.db", b"one")
            bundle.writestr("save/state.db", b"two")
    with pytest.raises(SaveArchiveError, match="Duplicate archive path"):
        inspect_archive(archive)


def test_inspection_rejects_symlink_members(tmp_path):
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("save/state.db")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr(link, "target")
    with pytest.raises(SaveArchiveError, match="Non-regular archive member"):
        inspect_archive(archive)


def test_inspection_enforces_compression_ratio_limit(tmp_path):
    archive = tmp_path / "bomb.zip"
    content = b"0" * 20_000
    manifest = _manifest_for("core_memory.json", content)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("save/core_memory.json", content)
    with pytest.raises(SaveArchiveError, match="compression ratio"):
        inspect_archive(archive, limits=ArchiveLimits(max_compression_ratio=2))


def test_extract_rejects_invalid_sqlite_and_cleans_staging(tmp_path):
    archive = tmp_path / "bad-db.zip"
    content = b"not sqlite"
    manifest = _manifest_for("campaign.db", content)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("save/campaign.db", content)
    destination = tmp_path / "staging"
    with pytest.raises(SaveArchiveError, match="Invalid SQLite"):
        extract_archive(archive, destination)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_snapshot_rejects_database_schema_newer_than_application(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("Future")
    database = source / "campaign.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_versions "
            "(component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_versions VALUES ('current_state', 999)"
        )
    with pytest.raises(SaveArchiveError, match="newer than this application"):
        export_save(source, tmp_path / "future.zip")


def test_snapshot_rejects_symlink_in_portable_subtree(tmp_path):
    source = tmp_path / "source"
    runtime = source / ".runtime"
    runtime.mkdir(parents=True)
    target = tmp_path / "outside.db"
    _make_database(target)
    (runtime / "linked.db").symlink_to(target)
    with pytest.raises(SaveArchiveError, match="Symbolic links"):
        create_consistent_snapshot(source, tmp_path / "snapshot")

def test_legacy_archive_files_are_allowed_for_import(tmp_path):
    from app.runtime.save_archive import _is_allowed_relative_path

    assert _is_allowed_relative_path("current_state.db") is True
    assert _is_allowed_relative_path("continuity.db") is True
    assert _is_allowed_relative_path("campaign.db") is True


def test_legacy_zip_import_migrates_to_campaign_db(tmp_path):
    import json
    import zipfile

    from app.runtime.save_archive import extract_archive
    from app.runtime.save_manifest import SaveManifestRepository

    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("旧档")
    # Seed a legacy current_state.db with a minimal current_state row.
    db = source / "current_state.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO current_state(id, state_json, updated_at) VALUES (1, ?, '')",
            (
                json.dumps({
                    "scene": {"scene_id": "sc1", "location": "旧地"},
                    "player": {"name": "旧人"},
                }, ensure_ascii=False),
            ),
        )
        conn.commit()
    save_manifest = (source / "save_manifest.json").read_bytes()
    manifest = {
        "format_version": 1,
        "files": [
            {
                "path": "current_state.db",
                "size": db.stat().st_size,
                "sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
            },
            {
                "path": "save_manifest.json",
                "size": len(save_manifest),
                "sha256": hashlib.sha256(save_manifest).hexdigest(),
            },
        ],
        "metadata": {},
    }
    archive = tmp_path / "legacy.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        )
        bundle.write(db, "save/current_state.db")
        bundle.writestr("save/save_manifest.json", save_manifest)
    destination = tmp_path / "staging"
    extract_archive(archive, destination)
    assert (destination / "campaign.db").is_file()
    assert not (destination / "current_state.db").exists()


def test_legacy_zip_with_snapshot_migrates_snapshot_to_campaign_db(tmp_path):
    import json
    import zipfile

    from app.runtime.save_archive import extract_archive
    from app.runtime.save_manifest import SaveManifestRepository

    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("旧快照档")
    db = source / "current_state.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO current_state(id, state_json, updated_at) VALUES (1, ?, '')",
            (
                json.dumps({
                    "scene": {"scene_id": "sc1", "location": "旧地"},
                    "player": {"name": "旧人"},
                }, ensure_ascii=False),
            ),
        )
        conn.commit()
    snapshot = source / ".snapshots" / "undo-one"
    snapshot.mkdir(parents=True)
    snap_db = snapshot / "current_state.db"
    with sqlite3.connect(snap_db) as conn:
        conn.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO current_state(id, state_json, updated_at) VALUES (1, ?, '')",
            (
                json.dumps({
                    "scene": {"scene_id": "sc1", "location": "旧快照"},
                    "player": {"name": "旧人"},
                }, ensure_ascii=False),
            ),
        )
        conn.commit()
    (snapshot / "manifest.json").write_text(
        json.dumps({"files": ["current_state.db"]}),
        encoding="utf-8",
    )
    save_manifest = (source / "save_manifest.json").read_bytes()

    files = [
        ("current_state.db", db),
        ("save_manifest.json", save_manifest),
    ]
    manifest_files = []
    for path, item in files:
        data = item if isinstance(item, bytes) else item.read_bytes()
        manifest_files.append({
            "path": path,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest_files.append({
        "path": ".snapshots/undo-one/current_state.db",
        "size": snap_db.stat().st_size,
        "sha256": hashlib.sha256(snap_db.read_bytes()).hexdigest(),
    })
    manifest_files.append({
        "path": ".snapshots/undo-one/manifest.json",
        "size": (snapshot / "manifest.json").stat().st_size,
        "sha256": hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest(),
    })
    archive = tmp_path / "legacy-snap.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps({
                "format_version": 1,
                "files": manifest_files,
                "metadata": {},
            }, ensure_ascii=False, sort_keys=True),
        )
        bundle.write(db, "save/current_state.db")
        bundle.writestr("save/save_manifest.json", save_manifest)
        bundle.write(snap_db, "save/.snapshots/undo-one/current_state.db")
        bundle.write(snapshot / "manifest.json", "save/.snapshots/undo-one/manifest.json")

    destination = tmp_path / "staging"
    extract_archive(archive, destination)
    assert (destination / "campaign.db").is_file()
    assert (destination / ".snapshots" / "undo-one" / "campaign.db").is_file()
    assert not (destination / ".snapshots" / "undo-one" / "current_state.db").exists()
    snapshot_manifest = json.loads(
        (destination / ".snapshots" / "undo-one" / "manifest.json").read_text()
    )
    assert snapshot_manifest["files"] == ["campaign.db"]


def test_legacy_snapshot_rejects_non_string_manifest(tmp_path):
    import json
    import zipfile

    from app.runtime.save_archive import extract_archive
    from app.runtime.save_manifest import SaveManifestRepository

    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("坏快照")
    db = source / "current_state.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT '')"
        )
        conn.commit()
    snapshot = source / ".snapshots" / "undo-one"
    snapshot.mkdir(parents=True)
    snap_db = snapshot / "current_state.db"
    with sqlite3.connect(snap_db) as conn:
        conn.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT '')"
        )
        conn.commit()
    (snapshot / "manifest.json").write_text(
        json.dumps({"files": [{"path": "current_state.db"}]}),
        encoding="utf-8",
    )
    save_manifest = (source / "save_manifest.json").read_bytes()
    manifest_files = [
        {
            "path": "current_state.db",
            "size": db.stat().st_size,
            "sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
        },
        {
            "path": "save_manifest.json",
            "size": len(save_manifest),
            "sha256": hashlib.sha256(save_manifest).hexdigest(),
        },
        {
            "path": ".snapshots/undo-one/current_state.db",
            "size": snap_db.stat().st_size,
            "sha256": hashlib.sha256(snap_db.read_bytes()).hexdigest(),
        },
        {
            "path": ".snapshots/undo-one/manifest.json",
            "size": (snapshot / "manifest.json").stat().st_size,
            "sha256": hashlib.sha256(
                (snapshot / "manifest.json").read_bytes()
            ).hexdigest(),
        },
    ]
    archive = tmp_path / "bad-snap.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps({
                "format_version": 1,
                "files": manifest_files,
                "metadata": {},
            }, ensure_ascii=False, sort_keys=True),
        )
        bundle.write(db, "save/current_state.db")
        bundle.writestr("save/save_manifest.json", save_manifest)
        bundle.write(snap_db, "save/.snapshots/undo-one/current_state.db")
        bundle.write(
            snapshot / "manifest.json",
            "save/.snapshots/undo-one/manifest.json",
        )
    with pytest.raises(SaveArchiveError, match="string list"):
        extract_archive(archive, tmp_path / "bad-staging")
