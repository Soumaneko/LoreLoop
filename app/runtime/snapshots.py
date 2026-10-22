from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from app.core.time import utc_now

from .campaign_migration import validate_campaign_database


SNAPSHOT_DATA_FILES = frozenset({
    "campaign.db",
    "core_memory.json",
    "initialization.json",
})
SNAPSHOT_ARCHIVE_FILES = SNAPSHOT_DATA_FILES | {"manifest.json"}


class SnapshotError(RuntimeError):
    """An Undo/Redo snapshot is malformed or cannot be installed safely."""


def capture_snapshot(data_dir: Path, destination: Path) -> None:
    data_dir = Path(data_dir)
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    files: list[str] = []
    campaign = data_dir / "campaign.db"
    if campaign.is_file():
        source_connection = sqlite3.connect(str(campaign))
        target_connection = sqlite3.connect(str(destination / "campaign.db"))
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        files.append("campaign.db")

    for name in ("core_memory.json", "initialization.json"):
        source = data_dir / name
        if source.is_file():
            shutil.copy2(source, destination / name)
            files.append(name)

    (destination / "manifest.json").write_text(
        json.dumps({"created_at": utc_now(), "files": files}, indent=2),
        encoding="utf-8",
    )


def normalize_snapshot(snapshot_dir: Path) -> list[str]:
    """Validate and normalize a snapshot manifest to the current format."""
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SnapshotError(f"Snapshot is missing manifest.json: {snapshot_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(
            f"Snapshot manifest is not valid JSON: {snapshot_dir}"
        ) from exc
    if not isinstance(manifest, dict):
        raise SnapshotError(f"Snapshot manifest must be an object: {snapshot_dir}")
    files = manifest.get("files")
    if not isinstance(files, list) or not all(
        isinstance(item, str) for item in files
    ):
        raise SnapshotError(
            f"Snapshot manifest files must be a string list: {snapshot_dir}"
        )

    for name in files:
        if name not in SNAPSHOT_DATA_FILES:
            raise SnapshotError(
                f"Snapshot manifest contains unsupported file: {name!r} in {snapshot_dir}"
            )

    return list(dict.fromkeys(files))


def install_snapshot(
    data_dir: Path,
    source: Path,
) -> None:
    """Validate, stage, and install one snapshot into a quiesced campaign."""
    data_dir = Path(data_dir)
    source = Path(source)
    expected = list(dict.fromkeys(normalize_snapshot(source)))

    staged: list[tuple[Path, Path]] = []
    try:
        for name in expected:
            snapshot_file = source / name
            destination = data_dir / name
            if not snapshot_file.exists():
                raise SnapshotError(f"Snapshot file missing: {snapshot_file}")
            temporary = data_dir / f".{name}.{uuid.uuid4().hex}.tmp"
            staged.append((destination, temporary))
            shutil.copy2(snapshot_file, temporary)
            if snapshot_file.suffix == ".db":
                validate_campaign_database(temporary)
            else:
                try:
                    json.loads(temporary.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise SnapshotError(f"Snapshot JSON invalid: {snapshot_file}") from exc
            _fsync_file(temporary)

        current = data_dir / "campaign.db"
        if current.exists() and current.name not in expected:
            _remove_sqlite_files(current)
        for name in ("core_memory.json", "initialization.json"):
            current = data_dir / name
            if current.exists() and name not in expected:
                current.unlink()

        for destination, temporary in staged:
            if destination.suffix == ".db":
                for suffix in ("-wal", "-shm", "-journal"):
                    Path(f"{destination}{suffix}").unlink(missing_ok=True)
            os.replace(temporary, destination)
        _fsync_directory(data_dir)
    finally:
        for _destination, temporary in staged:
            temporary.unlink(missing_ok=True)


def _remove_sqlite_files(database: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    database.unlink(missing_ok=True)


def _fsync_file(path: Path) -> None:
    try:
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())
    except OSError:
        pass


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(str(path), os.O_DIRECTORY)
    except (OSError, NotADirectoryError):
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        os.close(descriptor)
