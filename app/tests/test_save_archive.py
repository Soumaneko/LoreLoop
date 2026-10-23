import json
from contextlib import closing
import sqlite3
import stat
import zipfile
from pathlib import Path
import pytest
from app.runtime.save_archive import ArchiveLimits, SaveArchiveError, ClientValidationError, create_consistent_snapshot, export_save, extract_archive, InvalidSaveArchive
from app.runtime.save_manifest import SaveManifestRepository
from app.runtime.campaign_migration import ensure_campaign_database
from app.runtime.paths import CampaignPaths
from app.runtime.turn_history import _ensure_control_schema


def _make_database(path: Path, value: str = "saved") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE values_for_test (value TEXT)")
        connection.execute("INSERT INTO values_for_test VALUES (?)", (value,))
        connection.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL)"
        )
        connection.commit()


def _make_campaign_database(path: Path, value: str = "saved") -> None:
    ensure_campaign_database(CampaignPaths.from_data_dir(path.parent))
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE values_for_test (value TEXT)")
        conn.execute("INSERT INTO values_for_test VALUES (?)", (value,))


def _make_control_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_control_schema(path)


def _write_external_archive(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in source.rglob("*"):
            if path.is_file() and path.suffix in {".db", ".json"}:
                bundle.write(path, f"save/{path.relative_to(source).as_posix()}")


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
        _make_control_database(source / ".runtime" / "control.db")

        destination = tmp_path / "snapshot"
        files = create_consistent_snapshot(source, destination)
        assert files == [".runtime/control.db", "campaign.db"]
        assert not list(destination.glob("*-wal"))
        assert not list(destination.glob("*-shm"))
        with closing(sqlite3.connect(destination / "campaign.db")) as copied, copied:
            assert copied.execute("SELECT value FROM values_for_test").fetchone()[0] == "from-wal"
        with closing(sqlite3.connect(source / ".runtime" / "control.db")) as control, control:
            control.execute("UPDATE control_state SET restore_target='.snapshots/unfinished'")
        with pytest.raises(ClientValidationError, match="未完成的撤销"):
            create_consistent_snapshot(source, tmp_path / "unfinished-copy")
    finally:
        connection.close()


def test_export_and_extract_round_trip_allowed_save_files(tmp_path):
    source = tmp_path / "source#%20name"
    source.mkdir()
    SaveManifestRepository(source).create("Alpha")
    _make_campaign_database(source / "campaign.db")
    _make_control_database(source / ".runtime" / "control.db")
    _make_campaign_database(source / ".snapshots" / "undo-one" / "campaign.db", "old")
    (source / ".snapshots" / "undo-one" / "manifest.json").write_text(
        '{"files": ["campaign.db"]}', encoding="utf-8"
    )
    (source / "ignored.txt").write_text("not portable", encoding="utf-8")
    (source / ".snapshots" / "undo-one" / "settings.json").write_text("{}")

    archive = tmp_path / "save.zip"
    exported = export_save(source, archive)
    assert "ignored.txt" not in exported.files
    assert ".snapshots/undo-one/settings.json" not in exported.files
    with zipfile.ZipFile(archive) as bundle:
        assert "save/campaign.db" in bundle.namelist()
        assert "save/ignored.txt" not in bundle.namelist()

    destination = tmp_path / "imported#%20name"
    extracted = extract_archive(archive, destination)
    assert extracted == exported
    assert SaveManifestRepository(destination).load().display_name == "Alpha"
    with closing(sqlite3.connect(destination / ".snapshots" / "undo-one" / "campaign.db")) as db, db:
        assert db.execute("SELECT value FROM values_for_test").fetchone()[0] == "old"


def test_snapshot_preserves_portable_turn_history_paths(tmp_path):
    source = tmp_path / "old-save"
    snapshots = source / ".snapshots"
    snapshots.mkdir(parents=True)
    _make_database(source / "campaign.db")
    undo = snapshots / "undo-one"
    redo = snapshots / "redo-one"
    undo.mkdir()
    redo.mkdir()
    _make_database(undo / "campaign.db", "before")
    _make_database(redo / "campaign.db", "after")
    (undo / "manifest.json").write_text('{"files": ["campaign.db"]}', encoding="utf-8")
    (redo / "manifest.json").write_text('{"files": ["campaign.db"]}', encoding="utf-8")
    history = source / ".runtime" / "control.db"
    _make_control_database(history)
    with closing(sqlite3.connect(history)) as connection, connection:
        connection.execute(
            "INSERT INTO turn_snapshots(snapshot_id,turn_id,turn_index,path,status,created_at,redo_path) "
            "VALUES ('s','t',1,?,'applied','now',?)",
            (".snapshots/undo-one", ".snapshots/redo-one"),
        )

    copied = tmp_path / "copy"
    create_consistent_snapshot(source, copied)
    with closing(sqlite3.connect(copied / ".runtime" / "control.db")) as connection, connection:
        path, redo_path = connection.execute(
            "SELECT path, redo_path FROM turn_snapshots"
        ).fetchone()
    assert path == ".snapshots/undo-one"
    assert redo_path == ".snapshots/redo-one"


@pytest.mark.parametrize("field, stored", [
    ("path", "/old/save/.snapshots/undo-one"),
    ("redo_path", r"C:\old\save\.snapshots\redo-one"),
    ("restore_target", "/old/save/.snapshots/undo-one"),
    ("restore_target", ".snapshots/../outside"),
    ("path", ".snapshots/missing"),
])
def test_import_rejects_unsafe_history_paths(tmp_path, field, stored):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("Paths")
    _make_campaign_database(source / "campaign.db")
    _make_control_database(source / ".runtime" / "control.db")
    with closing(sqlite3.connect(source / ".runtime" / "control.db")) as conn, conn:
        if field == "restore_target":
            conn.execute("UPDATE control_state SET restore_target=?", (stored,))
        else:
            conn.execute(
                "INSERT INTO turn_snapshots(snapshot_id,turn_id,turn_index,path,status,created_at,redo_path) "
                "VALUES ('s','t',1,?,'applied','now',?)",
                (stored if field == "path" else ".snapshots/undo-one", stored if field == "redo_path" else None),
            )
            if field == "redo_path":
                snapshot = source / ".snapshots" / "undo-one"
                snapshot.mkdir(parents=True)
                (snapshot / "manifest.json").write_text('{"files":["campaign.db"]}')
    archive = tmp_path / "paths.zip"
    _write_external_archive(source, archive)
    with pytest.raises(InvalidSaveArchive, match="(relative|outside|missing)"):
        extract_archive(archive, tmp_path / "destination")
    assert list((tmp_path / "destination").iterdir()) == []


@pytest.mark.parametrize("member", ["../escape.db", "/absolute.db", r"save\..\escape.db"])
def test_import_rejects_zip_slip_paths(tmp_path, member):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, b"bad")
    with pytest.raises(SaveArchiveError, match="Unsafe archive path"):
        extract_archive(archive, tmp_path / "staging")


def test_import_rejects_duplicate_members(tmp_path):
    archive = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("save/campaign.db", b"one")
            bundle.writestr("save/campaign.db", b"two")
    with pytest.raises(SaveArchiveError, match="Duplicate archive path"):
        extract_archive(archive, tmp_path / "staging")


def test_import_rejects_symlink_members(tmp_path):
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("save/campaign.db")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(link, "target")
    with pytest.raises(SaveArchiveError, match="Non-regular archive member"):
        extract_archive(archive, tmp_path / "staging")


def test_extract_rejects_invalid_sqlite_and_cleans_staging(tmp_path):
    archive = tmp_path / "bad-db.zip"
    content = b"not sqlite"
    control_path = tmp_path / "control.db"
    _make_control_database(control_path)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("save/campaign.db", content)
        bundle.write(control_path, "save/.runtime/control.db")
        manifest_dir = tmp_path / "manifest-source"
        manifest_dir.mkdir()
        SaveManifestRepository(manifest_dir).create("Broken")
        bundle.write(manifest_dir / "save_manifest.json", "save/save_manifest.json")
    destination = tmp_path / "staging"
    with pytest.raises(SaveArchiveError, match="invalid save data"):
        extract_archive(archive, destination)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("version", [999, "invalid"])
def test_import_rejects_incompatible_database_version(tmp_path, version):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("Future")
    _make_campaign_database(source / "campaign.db")
    _make_control_database(source / ".runtime" / "control.db")
    with closing(sqlite3.connect(source / "campaign.db")) as conn, conn:
        conn.execute("UPDATE schema_versions SET version=? WHERE component='current_state'", (version,))
    archive = tmp_path / "future.zip"
    export_save(source, archive)
    with pytest.raises(InvalidSaveArchive, match="invalid save data"):
        extract_archive(archive, tmp_path / "destination")


def test_snapshot_rejects_symlink_in_portable_subtree(tmp_path):
    source = tmp_path / "source"
    runtime = source / ".runtime"
    runtime.mkdir(parents=True)
    _make_database(source / "campaign.db")
    _make_control_database(runtime / "control.db")
    target = tmp_path / "outside.db"
    _make_database(target)
    (runtime / "linked.db").symlink_to(target)
    with pytest.raises(SaveArchiveError, match="Symbolic links"):
        create_consistent_snapshot(source, tmp_path / "snapshot")


def test_export_rejects_incomplete_current_layout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("Incomplete")
    archive = tmp_path / "incomplete.zip"

    with pytest.raises(
        SaveArchiveError, match="campaign.db and .runtime/control.db"
    ):
        export_save(source, archive)

    assert not archive.exists()


@pytest.mark.parametrize("damage", ["manifest", "missing_file", "database"])
def test_import_rejects_damaged_snapshot(tmp_path, damage):
    source = tmp_path / "source"
    source.mkdir()
    SaveManifestRepository(source).create("坏快照")
    _make_campaign_database(source / "campaign.db")
    _make_control_database(source / ".runtime" / "control.db")
    snapshot = source / ".snapshots" / "undo-one"
    snapshot.mkdir(parents=True)
    files = [{"path": "campaign.db"}] if damage == "manifest" else ["campaign.db"]
    (snapshot / "manifest.json").write_text(json.dumps({"files": files}))
    if damage == "manifest":
        _make_campaign_database(snapshot / "campaign.db")
    elif damage == "database":
        (snapshot / "campaign.db").write_bytes(b"broken")
    archive = tmp_path / "bad-snap.zip"
    _write_external_archive(source, archive)
    destination = tmp_path / "destination"
    with pytest.raises(InvalidSaveArchive):
        extract_archive(archive, destination)
    assert list(destination.iterdir()) == []


def test_import_rejects_oversized_member_without_extracting(tmp_path):
    archive = tmp_path / "oversized.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("save/campaign.db", b"12345")
    destination = tmp_path / "destination"
    with pytest.raises(InvalidSaveArchive):
        extract_archive(archive, destination, limits=ArchiveLimits(max_file_size=4))
    assert not destination.exists() or list(destination.iterdir()) == []
