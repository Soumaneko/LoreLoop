from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState
from app.runtime.initialization import InitializationRecord

from .campaign_migration import LEGACY_DB_FILES
from .save_manifest import SaveManifest
from .snapshots import SNAPSHOT_ARCHIVE_FILES, SnapshotError, normalize_snapshot
from .sqlite import SUPPORTED_SCHEMA_VERSIONS

_LEGACY_ROOT_DB_SET = set(LEGACY_DB_FILES)


ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_MANIFEST = "manifest.json"
ARCHIVE_SAVE_ROOT = "save"
_ALLOWED_SUFFIXES = {".db", ".json"}
_ALLOWED_SUBTREES = {".runtime", ".snapshots"}
_ROOT_FILES = {
    "campaign.db",
    "core_memory.json",
    "initialization.json",
    "save_manifest.json",
    "settings.json",
}
class SaveArchiveError(ValueError):
    """The save or archive violates the portable save format."""


@dataclass(frozen=True)
class ArchiveLimits:
    max_files: int = 10_000
    max_file_size: int = 512 * 1024 * 1024
    max_total_size: int = 2 * 1024 * 1024 * 1024
    max_compression_ratio: int = 200


@dataclass(frozen=True)
class ArchiveInspection:
    format_version: int
    files: tuple[str, ...]
    total_size: int
    metadata: dict[str, Any]


def _assert_current_layout(save_dir: Path) -> None:
    """Require the current dual-database layout and reject legacy markers."""
    save_dir = Path(save_dir).resolve()
    # root ten DBs
    for name in _LEGACY_ROOT_DB_SET:
        if (save_dir / name).is_file():
            raise SaveArchiveError(
                f"unsupported legacy layout: save contains legacy file {name}"
            )
    if (save_dir / ".runtime" / "turn_history.db").is_file():
        raise SaveArchiveError(
            "unsupported legacy layout: save contains legacy .runtime/turn_history.db"
        )
    if (save_dir / ".runtime" / "director_mutation.json").is_file():
        raise SaveArchiveError(
            "unsupported legacy layout: save contains legacy .runtime/director_mutation.json"
        )
    # snapshots with legacy DBs or settings.json
    snapshots_dir = save_dir / ".snapshots"
    if snapshots_dir.is_dir():
        for snapshot_dir in snapshots_dir.iterdir():
            if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
                continue
            for name in _LEGACY_ROOT_DB_SET:
                if (snapshot_dir / name).is_file():
                    raise SaveArchiveError(
                        f"unsupported legacy layout: snapshot {snapshot_dir.name} contains legacy file {name}"
                    )
            if (snapshot_dir / "settings.json").is_file():
                raise SaveArchiveError(
                    f"unsupported legacy layout: snapshot {snapshot_dir.name} contains legacy settings.json"
                )
    if not (save_dir / "campaign.db").is_file() or not (
        save_dir / ".runtime" / "control.db"
    ).is_file():
        raise SaveArchiveError(
            "Save must contain campaign.db and .runtime/control.db"
        )


def create_consistent_snapshot(source_dir: Path, destination_dir: Path) -> list[str]:
    """Copy a quiesced save into an empty staging directory.

    SQLite files are copied with the online backup API, never by copying their
    database, WAL, and SHM files independently. The caller must prevent writes
    that span multiple databases while this function runs.
    """
    source = Path(source_dir).resolve()
    destination = Path(destination_dir).resolve()
    if not source.is_dir() or source.is_symlink():
        raise SaveArchiveError(f"Save directory is not a regular directory: {source}")
    _assert_current_layout(source)
    _prepare_empty_directory(destination)

    for relative, item in _iter_save_files(source):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix == ".db":
            _backup_database(item, target)
            quick_check_database(target)
        else:
            _copy_regular_file(item, target)

    _normalize_snapshot_manifests(destination)
    rebase_history_paths(destination)
    return [relative.as_posix() for relative, _ in _iter_save_files(destination)]


def _normalize_snapshot_manifests(data_dir: Path) -> None:
    """Validate every snapshot manifest against the current whitelist."""
    snapshots_dir = Path(data_dir) / ".snapshots"
    for snapshot_dir in sorted(snapshots_dir.glob("*")):
        if not snapshot_dir.is_dir():
            continue
        try:
            normalize_snapshot(snapshot_dir)
        except SnapshotError as exc:
            raise SaveArchiveError(str(exc)) from exc


def export_save(
    source_dir: Path,
    archive_path: Path,
    *,
    metadata: dict[str, Any] | None = None,
) -> ArchiveInspection:
    """Write a complete archive to a new path.

    `archive_path` is deliberately not atomically installed: desktop/application
    code should pass a temporary path and rename it only after this returns.
    """
    archive = Path(archive_path)
    if archive.exists():
        raise FileExistsError(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="save-export-") as temporary:
        snapshot = Path(temporary) / "save"
        create_consistent_snapshot(Path(source_dir), snapshot)
        entries = _manifest_entries(snapshot)
        manifest = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "files": entries,
            "metadata": dict(metadata or {}),
        }
        try:
            with zipfile.ZipFile(
                archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as bundle:
                bundle.writestr(
                    ARCHIVE_MANIFEST,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
                )
                for entry in entries:
                    bundle.write(
                        snapshot / entry["path"],
                        f"{ARCHIVE_SAVE_ROOT}/{entry['path']}",
                    )
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
    return _inspection_from_manifest(manifest)


def inspect_archive(
    archive_path: Path, *, limits: ArchiveLimits = ArchiveLimits()
) -> ArchiveInspection:
    """Validate archive structure, resource limits, manifest, and checksums."""
    archive = Path(archive_path)
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = _safe_members(bundle, limits)
            manifest_info = next(
                (item for item in infos if item.filename == ARCHIVE_MANIFEST), None
            )
            if manifest_info is None:
                raise SaveArchiveError("Archive is missing manifest.json")
            try:
                manifest = json.loads(bundle.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SaveArchiveError("Archive manifest is not valid UTF-8 JSON") from exc
            expected = _validate_manifest(manifest)
            actual = {
                item.filename.removeprefix(f"{ARCHIVE_SAVE_ROOT}/"): item
                for item in infos
                if item.filename.startswith(f"{ARCHIVE_SAVE_ROOT}/")
                and not item.is_dir()
            }
            if set(actual) != set(expected):
                raise SaveArchiveError("Archive files do not match its manifest")
            for path, entry in expected.items():
                info = actual[path]
                if info.file_size != entry["size"]:
                    raise SaveArchiveError(f"Size mismatch for save/{path}")
                digest = hashlib.sha256()
                with bundle.open(info, "r") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != entry["sha256"]:
                    raise SaveArchiveError(f"Checksum mismatch for save/{path}")
    except (zipfile.BadZipFile, OSError) as exc:
        raise SaveArchiveError("File is not a readable ZIP archive") from exc
    return _inspection_from_manifest(manifest)


def extract_archive(
    archive_path: Path,
    destination_dir: Path,
    *,
    limits: ArchiveLimits = ArchiveLimits(),
) -> ArchiveInspection:
    """Validate and extract an archive into an empty staging directory.

    All SQLite databases receive `PRAGMA quick_check`; turn-history paths are
    rebased to the staging directory.  Atomic installation is the caller's job.
    """
    inspection = inspect_archive(archive_path, limits=limits)
    destination = Path(destination_dir).resolve()
    _prepare_empty_directory(destination)
    wanted = set(inspection.files)
    try:
        with zipfile.ZipFile(archive_path, "r") as bundle:
            infos = _safe_members(bundle, limits)
            for info in infos:
                prefix = f"{ARCHIVE_SAVE_ROOT}/"
                if info.is_dir() or not info.filename.startswith(prefix):
                    continue
                relative = info.filename.removeprefix(prefix)
                if relative not in wanted:
                    raise SaveArchiveError(f"Unexpected archive member: {info.filename}")
                target = destination / PurePosixPath(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info, "r") as source, target.open("xb") as output:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        output.write(block)
        for database in destination.rglob("*.db"):
            quick_check_database(database)
        _normalize_snapshot_manifests(destination)
        rebase_history_paths(destination)
        _validate_save_payload(destination)
    except BaseException:
        _remove_staging_contents(destination)
        raise
    return inspection


def quick_check_database(database: Path) -> None:
    path = Path(database)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute("PRAGMA quick_check").fetchall()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "schema_versions" in tables:
                versions = connection.execute(
                    "SELECT component, version FROM schema_versions"
                ).fetchall()
                for component, version in versions:
                    supported = SUPPORTED_SCHEMA_VERSIONS.get(str(component))
                    if supported is None:
                        raise SaveArchiveError(
                            f"Unknown database schema component: {component}"
                        )
                    if int(version) > supported:
                        raise SaveArchiveError(
                            f"Database schema {component} is newer than this application"
                        )
    except sqlite3.DatabaseError as exc:
        raise SaveArchiveError(f"Invalid SQLite database: {path.name}") from exc
    if rows != [("ok",)]:
        raise SaveArchiveError(f"SQLite quick_check failed for {path.name}: {rows!r}")


def rebase_history_paths(data_dir: Path) -> None:
    """Validate and normalize portable Undo/Redo snapshot paths."""
    root = Path(data_dir).resolve()
    database = root / ".runtime" / "control.db"
    if not database.is_file():
        return
    label = database.name
    connection = sqlite3.connect(str(database))
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "turn_snapshots" not in tables:
            raise SaveArchiveError(f"{label} has no turn_snapshots table")
        rows = connection.execute(
            "SELECT sequence, path, redo_path FROM turn_snapshots"
        ).fetchall()
        for sequence, path, redo_path in rows:
            rebased_path = _portable_snapshot_path(path, root)
            rebased_redo = (
                _portable_snapshot_path(redo_path, root)
                if redo_path is not None
                else None
            )
            connection.execute(
                "UPDATE turn_snapshots SET path=?, redo_path=? WHERE sequence=?",
                (rebased_path, rebased_redo, sequence),
            )
        connection.commit()
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        raise SaveArchiveError("Invalid turn-history database") from exc
    finally:
        connection.close()
    quick_check_database(database)


def _portable_snapshot_path(value: str, root: Path) -> str:
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if posix_path.is_absolute() or windows_path.is_absolute():
        raise SaveArchiveError("Turn-history snapshot paths must be relative")
    if "\\" in value:
        raise SaveArchiveError("Turn-history snapshot paths must use forward slashes")
    relative = Path(*posix_path.parts)
    if not relative.parts or relative.parts[0] != ".snapshots":
        raise SaveArchiveError("Turn-history path is outside .snapshots")
    target = (root / relative).resolve()
    try:
        target.relative_to((root / ".snapshots").resolve())
    except ValueError as exc:
        raise SaveArchiveError("Turn-history path escapes .snapshots") from exc
    if not target.is_dir():
        raise SaveArchiveError("Turn-history snapshot directory is missing")
    return target.relative_to(root).as_posix()


def _iter_save_files(root: Path) -> Iterable[tuple[Path, Path]]:
    candidates = sorted(root.iterdir())
    for item in candidates:
        if item.is_symlink():
            raise SaveArchiveError(f"Symbolic links are not allowed: {item.name}")
        if item.is_file():
            if item.name.endswith(("-wal", "-shm")):
                continue
            relative = Path(item.name)
            if _is_allowed_for_export(relative.as_posix()):
                yield relative, item
            continue
        if not item.is_dir() or item.name not in _ALLOWED_SUBTREES:
            continue
        for nested in sorted(item.rglob("*")):
            if nested.is_symlink():
                raise SaveArchiveError(
                    f"Symbolic links are not allowed: {nested.relative_to(root)}"
                )
            if not nested.is_file() or nested.name.endswith(("-wal", "-shm")):
                continue
            relative = nested.relative_to(root)
            if _is_allowed_for_export(relative.as_posix()):
                yield relative, nested


def _backup_database(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    try:
        with closing(
            sqlite3.connect(source_uri, uri=True, timeout=30)
        ) as source_connection:
            with closing(
                sqlite3.connect(str(destination), timeout=30)
            ) as target_connection:
                source_connection.backup(target_connection)
                target_connection.commit()
                # `journal_mode` is persistent and is copied with the database.
                # A portable snapshot has no live WAL, so normalize the copy to
                # a self-contained rollback-journal database.
                target_connection.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.DatabaseError as exc:
        raise SaveArchiveError(f"Could not snapshot SQLite database: {source.name}") from exc


def _copy_regular_file(source: Path, destination: Path) -> None:
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            output_file.write(block)


def _manifest_entries(root: Path) -> list[dict[str, Any]]:
    entries = []
    for relative, item in _iter_save_files(root):
        entries.append(
            {
                "path": relative.as_posix(),
                "size": item.stat().st_size,
                "sha256": _sha256(item),
            }
        )
    return sorted(entries, key=lambda entry: entry["path"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_members(bundle: zipfile.ZipFile, limits: ArchiveLimits) -> list[zipfile.ZipInfo]:
    infos = bundle.infolist()
    if len(infos) > limits.max_files:
        raise SaveArchiveError("Archive contains too many files")
    names: set[str] = set()
    total = 0
    for info in infos:
        raw_name = getattr(info, "orig_filename", info.filename)
        name = info.filename
        if not raw_name or "\\" in raw_name:
            raise SaveArchiveError(f"Unsafe archive path: {raw_name!r}")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise SaveArchiveError(f"Unsafe archive path: {name!r}")
        canonical = path.as_posix().rstrip("/")
        if canonical in names:
            raise SaveArchiveError(f"Duplicate archive path: {name}")
        names.add(canonical)
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode) or (
            file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))
        ):
            raise SaveArchiveError(f"Non-regular archive member: {name}")
        if info.file_size > limits.max_file_size:
            raise SaveArchiveError(f"Archive member is too large: {name}")
        total += info.file_size
        if total > limits.max_total_size:
            raise SaveArchiveError("Archive expands beyond the size limit")
        if (info.file_size > 0 and info.compress_size == 0) or (
            info.compress_size > 0
            and info.file_size / info.compress_size > limits.max_compression_ratio
        ):
            raise SaveArchiveError(f"Suspicious compression ratio: {name}")
        if not (
            name == ARCHIVE_MANIFEST
            or name == f"{ARCHIVE_SAVE_ROOT}/"
            or name.startswith(f"{ARCHIVE_SAVE_ROOT}/")
        ):
            raise SaveArchiveError(f"Unexpected archive member: {name}")
    return infos


def _validate_manifest(manifest: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(manifest, dict):
        raise SaveArchiveError("Archive manifest must be an object")
    if manifest.get("format_version") != ARCHIVE_FORMAT_VERSION:
        raise SaveArchiveError("Unsupported archive format version")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise SaveArchiveError("Archive manifest files must be a list")
    expected: dict[str, dict[str, Any]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise SaveArchiveError("Invalid archive manifest file entry")
        path = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if _is_legacy_archive_path(path):
            raise SaveArchiveError(
                f"unsupported legacy layout: archive contains legacy {path}"
            )
        if not isinstance(path, str) or not _is_allowed_relative_path(path):
            raise SaveArchiveError(f"Invalid save path in manifest: {path!r}")
        if path in expected:
            raise SaveArchiveError(f"Duplicate save path in manifest: {path}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SaveArchiveError(f"Invalid size for {path}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise SaveArchiveError(f"Invalid checksum for {path}")
        expected[path] = entry
    metadata = manifest.get("metadata", {})
    if not isinstance(metadata, dict):
        raise SaveArchiveError("Archive manifest metadata must be an object")
    if "campaign.db" not in expected or ".runtime/control.db" not in expected:
        raise SaveArchiveError("Archive manifest must contain campaign.db and .runtime/control.db")
    return expected


def _is_allowed_relative_path(value: str) -> bool:
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        return False
    if len(path.parts) == 1:
        return path.name in _ROOT_FILES
    if path.parts[0] == ".runtime":
        return path.parts == (".runtime", "control.db")
    if path.parts[0] == ".snapshots" and len(path.parts) == 3:
        return path.name in SNAPSHOT_ARCHIVE_FILES
    return False


def _is_legacy_archive_path(value: object) -> bool:
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    if len(path.parts) == 1:
        return path.name in _LEGACY_ROOT_DB_SET
    if path.parts in {
        (".runtime", "turn_history.db"),
        (".runtime", "director_mutation.json"),
    }:
        return True
    return (
        len(path.parts) == 3
        and path.parts[0] == ".snapshots"
        and (path.name in _LEGACY_ROOT_DB_SET or path.name == "settings.json")
    )


def _is_allowed_for_export(value: str) -> bool:
    return _is_allowed_relative_path(value)


def _validate_save_payload(data_dir: Path) -> None:
    manifest_path = data_dir / "save_manifest.json"
    if not manifest_path.is_file():
        raise SaveArchiveError("Archive does not contain save_manifest.json")
    if not (data_dir / "campaign.db").is_file() or not (data_dir / ".runtime" / "control.db").is_file():
        raise SaveArchiveError("Archive does not contain campaign.db and .runtime/control.db")
    try:
        SaveManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
        core_memory = data_dir / "core_memory.json"
        if core_memory.is_file():
            CoreMemory.model_validate_json(core_memory.read_text(encoding="utf-8"))
        settings = data_dir / "settings.json"
        if settings.is_file() and not isinstance(
            json.loads(settings.read_text(encoding="utf-8")), dict
        ):
            raise ValueError("settings.json must contain an object")
        initialization = data_dir / "initialization.json"
        if initialization.is_file():
            InitializationRecord.model_validate_json(
                initialization.read_text(encoding="utf-8")
            )
        state_db = data_dir / "campaign.db"
        if state_db.is_file():
            uri = f"file:{state_db.resolve().as_posix()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                row = connection.execute(
                    "SELECT state_json FROM current_state WHERE id=1"
                ).fetchone()
            if row is not None:
                CurrentState.model_validate_json(row[0])
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError) as exc:
        raise SaveArchiveError("Archive contains invalid save data") from exc


def _inspection_from_manifest(manifest: dict[str, Any]) -> ArchiveInspection:
    entries = manifest["files"]
    return ArchiveInspection(
        format_version=manifest["format_version"],
        files=tuple(entry["path"] for entry in entries),
        total_size=sum(entry["size"] for entry in entries),
        metadata=dict(manifest.get("metadata", {})),
    )


def _prepare_empty_directory(destination: Path) -> None:
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise SaveArchiveError(f"Staging path is not a directory: {destination}")
        if any(destination.iterdir()):
            raise SaveArchiveError(f"Staging directory is not empty: {destination}")
    else:
        destination.mkdir(parents=True)


def _remove_staging_contents(destination: Path) -> None:
    if not destination.is_dir() or destination.is_symlink():
        return
    for item in destination.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink(missing_ok=True)
