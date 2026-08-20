from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .paths import (
    ACTIVE_SAVE_FILE,
    SAVES_DIR,
    CampaignPaths,
    resolve_active_save_id,
    validate_save_name,
)
from .campaign_migration import ensure_campaign_database
from .initialization import InitializationStore
from .save_archive import (
    ArchiveInspection,
    create_consistent_snapshot,
    export_save as write_save_archive,
    extract_archive,
    inspect_archive,
)
from .save_manifest import (
    SaveManifestRepository,
    load_save_metadata,
)
from .session import CampaignRuntime, get_campaign_runtime
from .turn_history import CampaignHistory


@dataclass(frozen=True)
class SaveInfo:
    save_id: str
    name: str
    active: bool
    initialized: bool
    last_played_at: str
    turn_count: int
    protagonist_name: str | None
    location: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SaveManager:
    """Own local save identity, lifecycle operations, and active runtime binding."""

    def __init__(
        self,
        root: str | Path = SAVES_DIR,
        *,
        runtime: CampaignRuntime | None = None,
        active_file: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.runtime = runtime or get_campaign_runtime()
        if active_file is not None:
            self.active_file = Path(active_file)
        elif self.root == SAVES_DIR:
            self.active_file = ACTIVE_SAVE_FILE
        else:
            self.active_file = self.root / ".active_save"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self._cleanup_staging()
        if not self._save_directories():
            runtime_path = self.runtime.paths.data_dir
            if (
                runtime_path.parent.resolve() == self.root.resolve()
                and self._is_save_id(runtime_path.name)
                and runtime_path.is_dir()
            ):
                SaveManifestRepository(runtime_path).create(
                    "默认存档",
                    save_id=runtime_path.name,
                )
            else:
                self._create_directory("默认存档")
        active_id = self._configured_active_id()
        if (
            active_id != self.runtime.campaign_id
            or self.runtime.paths.data_dir != self._save_path(active_id)
        ):
            self.runtime.pending_turns.clear(discard=False)
            self.runtime.pending_openings.clear(discard=False)
            self.runtime.reconfigure(
                active_id,
                CampaignPaths.from_data_dir(self._save_path(active_id)),
            )
        self.runtime.configure_save_touch(self.touch_active)
        self._write_active_id(active_id)

    @property
    def active_id(self) -> str:
        return self.runtime.campaign_id

    def list(self) -> list[SaveInfo]:
        with self._lock:
            saves = [self._describe(path) for path in self._save_directories()]
        saves.sort(key=lambda item: item.name.casefold())
        saves.sort(key=lambda item: item.last_played_at, reverse=True)
        saves.sort(key=lambda item: item.active, reverse=True)
        return saves

    def create(
        self,
        name: str,
        *,
        activate: bool = True,
    ) -> SaveInfo:
        with self.runtime.save_task(), self._lock:
            clean_name = self._available_name(name)
            path = self._create_directory(clean_name)
            save_id = path.name
            try:
                if activate:
                    self._switch_locked(save_id)
                return self._describe(path)
            except BaseException:
                if self.active_id != save_id:
                    shutil.rmtree(path, ignore_errors=True)
                raise

    def switch(self, save_id: str) -> SaveInfo:
        with self.runtime.save_task(), self._lock:
            return self._switch_locked(self._validate_save_id(save_id))

    def rename(self, save_id: str, name: str) -> SaveInfo:
        clean_id = self._validate_save_id(save_id)
        with self._lock:
            clean_name = self._available_name(name, excluding=clean_id)
            path = self._require_save(clean_id)
            SaveManifestRepository(path).rename(clean_name)
            return self._describe(path)

    def copy(self, save_id: str, name: str = "") -> SaveInfo:
        source_id = self._validate_save_id(save_id)
        with self.runtime.save_task(), self._lock:
            self._require_no_pending_source(source_id, "copy")
            source = self._require_save(source_id)
            # A save that has never been opened since Phase 2 may still be in
            # the legacy ten-database layout. Copying it without migrating would
            # silently drop all canonical data because snapshots only accept
            # campaign.db.
            ensure_campaign_database(CampaignPaths.from_data_dir(source))
            if str(name).strip():
                clean_name = self._available_name(name)
            else:
                original = SaveManifestRepository(source).load().display_name
                candidate = f"{original} - 副本"
                suffix = 2
                while True:
                    try:
                        clean_name = self._available_name(candidate)
                        break
                    except ValueError:
                        candidate = f"{original} - 副本 {suffix}"
                        suffix += 1
            destination_id = str(uuid4())
            destination = self._save_path(destination_id)
            staging = Path(tempfile.mkdtemp(prefix=".copy-", dir=self.root))
            try:
                create_consistent_snapshot(source, staging)
                (staging / "save_manifest.json").unlink(missing_ok=True)
                SaveManifestRepository(staging).create(
                    clean_name,
                    save_id=destination_id,
                )
                os.replace(staging, destination)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            return self._describe(destination)

    def delete(
        self,
        save_id: str,
        *,
        confirmation_name: str,
        replacement_save_id: str | None = None,
    ) -> dict[str, Any]:
        clean_id = self._validate_save_id(save_id)
        with self.runtime.save_task(), self._lock:
            path = self._require_save(clean_id)
            manifest = SaveManifestRepository(path).load()
            if confirmation_name != manifest.display_name:
                raise ValueError("The confirmation name does not match the save")
            if len(self._save_directories()) <= 1:
                raise RuntimeError("The last remaining save cannot be deleted")
            if clean_id == self.active_id:
                if self.runtime.has_pending_content():
                    raise RuntimeError(
                        "Confirm or discard pending content before deleting this save"
                    )
                replacement = (
                    self._validate_save_id(replacement_save_id)
                    if replacement_save_id
                    else next(
                        item.name for item in self._save_directories()
                        if item.name != clean_id
                    )
                )
                if replacement == clean_id:
                    raise ValueError("Replacement save must be different")
                self._switch_locked(replacement)
            trash = self.root / f".trash-{uuid4()}"
            os.replace(path, trash)
            try:
                shutil.rmtree(trash)
            except OSError:
                # Hidden trash is excluded from all save enumeration and can be
                # cleaned on the next application start.
                pass
            return self.payload()

    def export_to(self, save_id: str, destination: str | Path) -> ArchiveInspection:
        clean_id = self._validate_save_id(save_id)
        destination_path = Path(destination)
        with self.runtime.save_task(), self._lock:
            self._require_no_pending_source(clean_id, "export")
            source = self._require_save(clean_id)
            # See copy(): migrate an unopened legacy save before snapshotting so
            # export does not silently omit all canonical data.
            ensure_campaign_database(CampaignPaths.from_data_dir(source))
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination_path.with_name(
                f".{destination_path.name}.{uuid4().hex}.tmp"
            )
            try:
                inspection = write_save_archive(source, temporary)
                os.replace(temporary, destination_path)
                return inspection
            finally:
                temporary.unlink(missing_ok=True)

    def inspect_import(self, archive: str | Path) -> ArchiveInspection:
        return inspect_archive(Path(archive))

    def import_from(self, archive: str | Path, name: str) -> SaveInfo:
        with self.runtime.save_task(), self._lock:
            clean_name = self._available_name(name)
            save_id = str(uuid4())
            destination = self._save_path(save_id)
            staging = Path(tempfile.mkdtemp(prefix=".import-", dir=self.root))
            try:
                extract_archive(Path(archive), staging)
                (staging / "save_manifest.json").unlink(missing_ok=True)
                SaveManifestRepository(staging).create(clean_name, save_id=save_id)
                os.replace(staging, destination)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            return self._describe(destination)

    def touch_active(self) -> None:
        with self._lock:
            SaveManifestRepository(self._require_save(self.active_id)).touch()

    def payload(self) -> dict[str, Any]:
        return {
            "active": self.active_id,
            "saves": [save.as_dict() for save in self.list()],
        }

    def _switch_locked(self, save_id: str) -> SaveInfo:
        path = self._require_save(save_id)
        if save_id != self.runtime.campaign_id:
            self.runtime.reconfigure(
                save_id,
                CampaignPaths.from_data_dir(path),
            )
        self._write_active_id(save_id)
        return self._describe(path)

    def _create_directory(self, display_name: str) -> Path:
        save_id = str(uuid4())
        path = self._save_path(save_id)
        path.mkdir(parents=False, exist_ok=False)
        try:
            SaveManifestRepository(path).create(display_name, save_id=save_id)
        except BaseException:
            shutil.rmtree(path, ignore_errors=True)
            raise
        return path

    def _available_name(self, name: str, *, excluding: str | None = None) -> str:
        clean = validate_save_name(name)
        folded = clean.casefold()
        for path in self._save_directories():
            if path.name == excluding:
                continue
            if SaveManifestRepository(path).load().display_name.casefold() == folded:
                raise ValueError(f"Save name already exists: {clean}")
        return clean

    def _describe(self, path: Path) -> SaveInfo:
        manifest = SaveManifestRepository(path).load()
        metadata = load_save_metadata(path)
        initialization = InitializationStore(
            CampaignPaths.from_data_dir(path).initialization_json
        ).load()
        return SaveInfo(
            save_id=manifest.save_id,
            name=manifest.display_name,
            active=manifest.save_id == self.runtime.campaign_id,
            initialized=(
                initialization is not None
                and initialization.phase == "completed"
            ),
            last_played_at=manifest.last_played_at,
            turn_count=metadata.turn_count,
            protagonist_name=metadata.protagonist_name,
            location=metadata.location,
        )

    def _save_directories(self) -> list[Path]:
        return sorted(
            (
                path for path in self.root.iterdir()
                if path.is_dir()
                and not path.is_symlink()
                and not path.name.startswith(".")
                and self._is_save_id(path.name)
                and (path / "save_manifest.json").is_file()
            ),
            key=lambda path: path.name,
        )

    def _require_save(self, save_id: str) -> Path:
        path = self._save_path(save_id)
        if not path.is_dir() or path.is_symlink():
            raise ValueError(f"Save not found: {save_id}")
        manifest = SaveManifestRepository(path).load()
        if manifest.save_id != save_id:
            raise ValueError("Save identity does not match its directory")
        return path

    def _save_path(self, save_id: str) -> Path:
        clean = self._validate_save_id(save_id)
        path = self.root / clean
        if path.parent.resolve() != self.root.resolve():
            raise ValueError("Save path must be inside the saves directory")
        return path

    def _configured_active_id(self) -> str:
        return resolve_active_save_id(
            root=self.root,
            active_file=self.active_file,
            policy="recent",
        )

    def _cleanup_staging(self) -> None:
        for path in self.root.iterdir():
            if path.is_dir() and any(
                path.name.startswith(prefix)
                for prefix in (".copy-", ".import-", ".trash-")
            ):
                shutil.rmtree(path, ignore_errors=True)

    def _write_active_id(self, save_id: str) -> None:
        temporary = self.active_file.with_name(f"{self.active_file.name}.tmp")
        temporary.write_text(save_id, encoding="utf-8")
        os.replace(temporary, self.active_file)

    def _require_no_pending_source(self, save_id: str, action: str) -> None:
        history = (
            self.runtime.history
            if save_id == self.active_id
            else CampaignHistory(self._save_path(save_id))
        )
        if history.pending_records():
            raise RuntimeError(
                f"Confirm or discard pending content before {action}ing this save"
            )

    @staticmethod
    def _is_save_id(value: str) -> bool:
        try:
            return str(UUID(value)) == value
        except (ValueError, AttributeError):
            return False

    def _validate_save_id(self, value: object) -> str:
        clean = str(value).strip()
        if not self._is_save_id(clean):
            raise ValueError("save_id must be a canonical UUID")
        return clean


_save_manager: SaveManager | None = None
_save_manager_lock = threading.Lock()


def _get_save_manager() -> SaveManager:
    global _save_manager
    if _save_manager is None:
        with _save_manager_lock:
            if _save_manager is None:
                _save_manager = SaveManager()
    return _save_manager


def list_saves() -> dict[str, Any]:
    return _get_save_manager().payload()


def create_save(payload: dict[str, Any]) -> dict[str, Any]:
    save = _get_save_manager().create(
        str(payload.get("name", "")),
        activate=bool(payload.get("activate", True)),
    )
    return {"save": save.as_dict(), **_get_save_manager().payload()}


def switch_save(payload: dict[str, Any]) -> dict[str, Any]:
    save = _get_save_manager().switch(str(payload.get("save_id", "")))
    return {"save": save.as_dict(), **_get_save_manager().payload()}


def rename_save(payload: dict[str, Any]) -> dict[str, Any]:
    save = _get_save_manager().rename(
        str(payload.get("save_id", "")),
        str(payload.get("name", "")),
    )
    return {"save": save.as_dict(), **_get_save_manager().payload()}


def copy_save(payload: dict[str, Any]) -> dict[str, Any]:
    save = _get_save_manager().copy(
        str(payload.get("save_id", "")),
        str(payload.get("name", "")),
    )
    return {"save": save.as_dict(), **_get_save_manager().payload()}


def delete_save(payload: dict[str, Any]) -> dict[str, Any]:
    return _get_save_manager().delete(
        str(payload.get("save_id", "")),
        confirmation_name=str(payload.get("confirmation_name", "")),
        replacement_save_id=(
            str(payload["replacement_save_id"])
            if payload.get("replacement_save_id") else None
        ),
    )


def export_save_archive(save_id: str, destination: str | Path) -> dict[str, Any]:
    inspection = _get_save_manager().export_to(save_id, destination)
    return {
        "format_version": inspection.format_version,
        "file_count": len(inspection.files),
        "total_size": inspection.total_size,
    }


def import_save_archive(archive: str | Path, name: str) -> dict[str, Any]:
    save = _get_save_manager().import_from(archive, name)
    return {"save": save.as_dict(), **_get_save_manager().payload()}
