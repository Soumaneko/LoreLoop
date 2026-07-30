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
    database = source / "memories.db"
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
        assert files == ["memories.db"]
        assert not list(destination.glob("*-wal"))
        assert not list(destination.glob("*-shm"))
        with sqlite3.connect(destination / "memories.db") as copied:
            assert copied.execute("SELECT value FROM values_for_test").fetchone()[0] == "from-wal"
    finally:
        connection.close()


def test_export_and_extract_round_trip_allowed_save_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("Alpha")
    _make_database(source / "memories.db")
    _make_database(source / "confirmed_history.db", "indexed")
    _make_database(source / ".snapshots" / "undo-one" / "memories.db", "old")
    (source / ".snapshots" / "undo-one" / "manifest.json").write_text(
        '{"files": ["memories.db"]}', encoding="utf-8"
    )
    (source / "ignored.txt").write_text("not portable", encoding="utf-8")

    archive = tmp_path / "save.zip"
    exported = export_save(source, archive, metadata={"display_name": "Alpha"})
    assert exported.metadata == {"display_name": "Alpha"}
    assert "ignored.txt" not in exported.files
    with zipfile.ZipFile(archive) as bundle:
        assert "manifest.json" in bundle.namelist()
        assert "save/memories.db" in bundle.namelist()
        assert "save/confirmed_history.db" in bundle.namelist()
        assert "save/ignored.txt" not in bundle.namelist()

    inspected = inspect_archive(archive)
    destination = tmp_path / "imported"
    extracted = extract_archive(archive, destination)
    assert extracted == inspected
    assert SaveManifestRepository(destination).load().display_name == "Alpha"
    with sqlite3.connect(destination / ".snapshots" / "undo-one" / "memories.db") as db:
        assert db.execute("SELECT value FROM values_for_test").fetchone()[0] == "old"


def test_snapshot_preserves_portable_turn_history_paths(tmp_path):
    source = tmp_path / "old-save"
    snapshots = source / ".snapshots"
    snapshots.mkdir(parents=True)
    undo = snapshots / "undo-one"
    redo = snapshots / "redo-one"
    undo.mkdir()
    redo.mkdir()
    (undo / "manifest.json").write_text("{}", encoding="utf-8")
    (redo / "manifest.json").write_text("{}", encoding="utf-8")
    history = source / ".runtime" / "turn_history.db"
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
    with sqlite3.connect(copied / ".runtime" / "turn_history.db") as connection:
        path, redo_path = connection.execute(
            "SELECT path, redo_path FROM turn_snapshots"
        ).fetchone()
    assert path == ".snapshots/undo-one"
    assert redo_path == ".snapshots/redo-one"


def test_rebase_rejects_absolute_history_path(tmp_path):
    data_dir = tmp_path / "save"
    history = data_dir / ".runtime" / "turn_history.db"
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
    history = data_dir / ".runtime" / "turn_history.db"
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
    history = data_dir / ".runtime" / "turn_history.db"
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
    manifest = _manifest_for("memories.db", content)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("save/memories.db", content)
    destination = tmp_path / "staging"
    with pytest.raises(SaveArchiveError, match="Invalid SQLite"):
        extract_archive(archive, destination)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_snapshot_rejects_database_schema_newer_than_application(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("Future")
    database = source / "current_state.db"
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
