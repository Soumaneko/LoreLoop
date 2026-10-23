from __future__ import annotations

import json
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from app.core.client_error import ClientValidationError
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState
from app.runtime.initialization import InitializationRecord

from .save_manifest import SaveManifest
from .snapshots import SNAPSHOT_ARCHIVE_FILES, normalize_snapshot
from .campaign_migration import validate_campaign_database
from .turn_history import _validate_control_database


ARCHIVE_SAVE_ROOT = "save"
_ALLOWED_SUBTREES = {".runtime", ".snapshots"}
_ROOT_FILES = {
    "campaign.db",
    "core_memory.json",
    "initialization.json",
    "initialization_draft.json",
    "save_manifest.json",
    "settings.json",
}


class SaveArchiveError(ValueError):
    """The save or archive violates the portable save format."""


class InvalidSaveArchive(SaveArchiveError, ClientValidationError):
    """A rejected archive payload, safe to explain to the desktop user."""

    def __init__(self, detail: str, *, reason: str = "文件内容或格式不符合存档要求"):
        super().__init__(detail)
        self.reason = reason

    @property
    def public_message(self) -> str:
        return f"存档校验失败（{self.reason}）：{self}。请重新选择有效的 ZIP 存档。"


@dataclass(frozen=True)
class ArchiveLimits:
    max_files: int = 10_000
    max_file_size: int = 512 * 1024 * 1024
    max_total_size: int = 2 * 1024 * 1024 * 1024
    max_compression_ratio: int = 200


@dataclass(frozen=True)
class ArchiveInspection:
    files: tuple[str, ...]
    total_size: int


def _assert_current_layout(save_dir: Path) -> None:
    """Require the current save's two databases."""
    save_dir = Path(save_dir).resolve()
    if not (save_dir / "campaign.db").is_file() or not (
        save_dir / ".runtime" / "control.db"
    ).is_file():
        raise InvalidSaveArchive(
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
    control_uri = (source / ".runtime" / "control.db").as_uri() + "?mode=ro"
    with closing(sqlite3.connect(control_uri, uri=True)) as conn:
        target = conn.execute("SELECT restore_target FROM control_state WHERE id=1").fetchone()
    if target and target[0] is not None:
        raise ClientValidationError("存档有未完成的撤销或重做，请先打开并恢复该存档，再复制或导出。")
    _prepare_empty_directory(destination)

    for relative, item in _iter_save_files(source):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix == ".db":
            _backup_database(item, target)
        else:
            shutil.copyfile(item, target)

    return [relative.as_posix() for relative, _ in _iter_save_files(destination)]


def export_save(source_dir: Path, archive_path: Path) -> ArchiveInspection:
    """Write a portable ZIP; the caller installs the completed archive."""
    archive = Path(archive_path)
    if archive.exists():
        raise FileExistsError(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="save-export-") as temporary:
        snapshot = Path(temporary) / "save"
        files = create_consistent_snapshot(Path(source_dir), snapshot)
        try:
            with zipfile.ZipFile(
                archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as bundle:
                for relative in files:
                    bundle.write(snapshot / relative, f"{ARCHIVE_SAVE_ROOT}/{relative}")
                return _inspection(bundle.infolist())
        except BaseException:
            archive.unlink(missing_ok=True)
            raise


def extract_archive(
    archive_path: Path,
    destination_dir: Path,
    *,
    limits: ArchiveLimits = ArchiveLimits(),
) -> ArchiveInspection:
    """Read the ZIP once into an isolated directory and validate before installation."""
    destination = Path(destination_dir).resolve()
    _prepare_empty_directory(destination)
    try:
        with zipfile.ZipFile(archive_path) as bundle:
            infos = _safe_members(bundle, limits)
            inspection = _inspection(infos)
            for info in infos:
                if info.is_dir():
                    continue
                relative = info.filename.removeprefix(f"{ARCHIVE_SAVE_ROOT}/")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        _validate_save_payload(destination)
    except BaseException as exc:
        _remove_staging_contents(destination)
        cause = exc.__cause__ or exc
        if isinstance(cause, OSError):
            raise cause
        if isinstance(cause, sqlite3.OperationalError) and not str(cause).startswith(
            ("no such table:", "no such column:")
        ):
            raise cause
        if isinstance(exc, InvalidSaveArchive):
            raise
        if isinstance(exc, (ValueError, RuntimeError, sqlite3.DatabaseError, zipfile.BadZipFile)):
            raise InvalidSaveArchive("Archive contains invalid save data", reason="存档内数据缺失、损坏或不符合当前格式") from exc
        raise
    return inspection


def _validate_history_paths(data_dir: Path) -> None:
    """Check all imported history references without changing their relative paths."""
    root = Path(data_dir).resolve()
    database = root / ".runtime" / "control.db"
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        rows = conn.execute("SELECT path, redo_path FROM turn_snapshots").fetchall()
        target = conn.execute("SELECT restore_target FROM control_state WHERE id=1").fetchone()
    for stored in [value for row in rows for value in row] + ([target[0]] if target else []):
        if stored is None:
            continue
        if not isinstance(stored, str):
            raise InvalidSaveArchive("Turn-history snapshot path must be text")
        path = PurePosixPath(stored)
        if path.is_absolute() or PureWindowsPath(stored).is_absolute():
            raise InvalidSaveArchive("Turn-history snapshot paths must be relative")
        if (
            "\\" in stored or ":" in stored or "\x00" in stored
            or len(path.parts) != 2 or path.parts[0] != ".snapshots"
            or any(part in (".", "..", "") for part in stored.split("/"))
        ):
            raise InvalidSaveArchive("Turn-history path is outside .snapshots")
        if not (root / path / "manifest.json").is_file():
            raise InvalidSaveArchive("Turn-history snapshot directory or manifest is missing")


def _iter_save_files(root: Path) -> Iterable[tuple[Path, Path]]:
    candidates = sorted(root.iterdir())
    for item in candidates:
        if item.is_symlink():
            raise SaveArchiveError(f"Symbolic links are not allowed: {item.name}")
        if item.is_file():
            if item.name.endswith(("-wal", "-shm")):
                continue
            relative = Path(item.name)
            if _is_allowed_relative_path(relative.as_posix()):
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
            if _is_allowed_relative_path(relative.as_posix()):
                yield relative, nested


def _backup_database(source: Path, destination: Path) -> None:
    source_uri = source.resolve().as_uri() + "?mode=ro"
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


def _safe_members(bundle: zipfile.ZipFile, limits: ArchiveLimits) -> list[zipfile.ZipInfo]:
    infos = bundle.infolist()
    if len(infos) > limits.max_files:
        raise InvalidSaveArchive("Archive contains too many files")
    names: set[str] = set()
    total = 0
    for info in infos:
        raw_name = getattr(info, "orig_filename", info.filename)
        name = info.filename
        if not raw_name or "\\" in raw_name or "\x00" in raw_name or ":" in raw_name:
            raise InvalidSaveArchive(f"Unsafe archive path: {raw_name!r}")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in (".", "..") for part in raw_name.split("/")):
            raise InvalidSaveArchive(f"Unsafe archive path: {name!r}")
        canonical = path.as_posix().rstrip("/").casefold()
        if canonical in names:
            raise InvalidSaveArchive(f"Duplicate archive path: {name}")
        names.add(canonical)
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode) or (
            file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))
        ):
            raise InvalidSaveArchive(f"Non-regular archive member: {name}")
        if info.file_size > limits.max_file_size:
            raise InvalidSaveArchive(f"Archive member is too large: {name}")
        total += info.file_size
        if total > limits.max_total_size:
            raise InvalidSaveArchive("Archive expands beyond the size limit")
        if (info.file_size > 0 and info.compress_size == 0) or (
            info.compress_size > 0
            and info.file_size / info.compress_size > limits.max_compression_ratio
        ):
            raise InvalidSaveArchive(f"Suspicious compression ratio: {name}")
        if not (
            name == f"{ARCHIVE_SAVE_ROOT}/"
            or (name.startswith(f"{ARCHIVE_SAVE_ROOT}/") and (
                info.is_dir() or _is_allowed_relative_path(name.removeprefix(f"{ARCHIVE_SAVE_ROOT}/"))
            ))
        ):
            raise InvalidSaveArchive(f"Unexpected archive member: {name}")
    return infos


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


def _validate_campaign_payload(data_dir: Path) -> None:
    validate_campaign_database(data_dir / "campaign.db")
    for name, schema in (("core_memory.json", CoreMemory), ("initialization.json", InitializationRecord)):
        path = data_dir / name
        if path.is_file():
            schema.model_validate_json(path.read_text(encoding="utf-8"))
    uri = (data_dir / "campaign.db").resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        row = conn.execute("SELECT state_json FROM current_state WHERE id=1").fetchone()
    if row is not None:
        CurrentState.model_validate_json(row[0])


def _validate_save_payload(data_dir: Path) -> None:
    from .initialization_draft import InitializationDraft, _draft_config_version

    _assert_current_layout(data_dir)
    manifest = data_dir / "save_manifest.json"
    SaveManifest.from_dict(json.loads(manifest.read_text(encoding="utf-8")))
    _validate_campaign_payload(data_dir)
    _validate_control_database(data_dir / ".runtime" / "control.db")
    _validate_history_paths(data_dir)
    settings = data_dir / "settings.json"
    if settings.is_file() and not isinstance(json.loads(settings.read_text(encoding="utf-8")), dict):
        raise InvalidSaveArchive("settings.json must contain an object")
    draft_path = data_dir / "initialization_draft.json"
    if draft_path.is_file():
        draft = InitializationDraft.model_validate_json(draft_path.read_text(encoding="utf-8"))
        if _draft_config_version(draft) not in (None, 4):
            raise InvalidSaveArchive("Initialization draft config must use version 4")
    for snapshot in sorted((data_dir / ".snapshots").glob("*")):
        if not snapshot.is_dir():
            continue
        files = normalize_snapshot(snapshot)
        if any(not (snapshot / name).is_file() for name in files):
            raise InvalidSaveArchive("Snapshot file is missing")
        _validate_campaign_payload(snapshot)


def _inspection(infos: list[zipfile.ZipInfo]) -> ArchiveInspection:
    files = [info for info in infos if not info.is_dir()]
    paths = tuple(info.filename.removeprefix(f"{ARCHIVE_SAVE_ROOT}/") for info in files)
    required = {"campaign.db", ".runtime/control.db", "save_manifest.json"}
    if not required.issubset(paths):
        raise InvalidSaveArchive("Archive must contain campaign.db, .runtime/control.db and save_manifest.json")
    return ArchiveInspection(files=paths, total_size=sum(info.file_size for info in files))


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
