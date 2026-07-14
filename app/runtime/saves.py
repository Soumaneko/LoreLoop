from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    ACTIVE_SAVE_FILE,
    DEFAULT_SAVE_NAME,
    SAVES_DIR,
    CampaignPaths,
    validate_save_name,
)
from .session import CampaignRuntime, get_campaign_runtime


@dataclass(frozen=True)
class SaveInfo:
    name: str
    active: bool
    initialized: bool
    modified_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SaveManager:
    """Own save directories and switch the process-wide campaign runtime safely."""

    def __init__(
        self,
        root: str | Path = SAVES_DIR,
        *,
        runtime: CampaignRuntime | None = None,
        active_file: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.runtime = runtime or get_campaign_runtime()
        self.active_file = Path(active_file) if active_file else self.root / ACTIVE_SAVE_FILE.name
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_save_directory(
            self.runtime.campaign_id or DEFAULT_SAVE_NAME
        )
        self._write_active_name(self.runtime.campaign_id)

    @property
    def active_name(self) -> str:
        return self.runtime.campaign_id

    def list(self) -> list[SaveInfo]:
        with self._lock:
            saves = [
                self._describe(path)
                for path in self.root.iterdir()
                if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")
            ]
        return sorted(saves, key=lambda item: (not item.active, item.name.casefold()))

    def create(
        self,
        name: str,
        *,
        activate: bool = True,
        discard_pending: bool = False,
    ) -> SaveInfo:
        clean = validate_save_name(name)
        with self._lock:
            if activate and self.runtime.has_pending_content() and not discard_pending:
                raise RuntimeError(
                    "Current save has pending content. Confirm it or explicitly "
                    "discard pending content before creating and switching saves."
                )
            path = self._save_path(clean)
            try:
                path.mkdir(parents=False, exist_ok=False)
            except FileExistsError as exc:
                raise ValueError(f"Save already exists: {clean}") from exc
            if activate:
                self._switch_locked(clean, discard_pending=discard_pending)
            return self._describe(path)

    def switch(self, name: str, *, discard_pending: bool = False) -> SaveInfo:
        clean = validate_save_name(name)
        with self._lock:
            return self._switch_locked(clean, discard_pending=discard_pending)

    def payload(self) -> dict[str, Any]:
        return {
            "active": self.active_name,
            "root": str(self.root.resolve()),
            "saves": [save.as_dict() for save in self.list()],
        }

    def _switch_locked(self, name: str, *, discard_pending: bool) -> SaveInfo:
        path = self._save_path(name)
        if not path.is_dir() or path.is_symlink():
            raise ValueError(f"Save not found: {name}")
        if name != self.runtime.campaign_id:
            self.runtime.reconfigure(
                name,
                CampaignPaths.from_data_dir(path),
                discard_pending=discard_pending,
            )
        self._write_active_name(name)
        return self._describe(path)

    def _save_path(self, name: str) -> Path:
        clean = validate_save_name(name)
        path = self.root / clean
        if path.parent.resolve() != self.root.resolve():
            raise ValueError("Save path must be inside the saves directory")
        return path

    def _ensure_save_directory(self, name: str) -> Path:
        path = self._save_path(validate_save_name(name))
        if path.is_symlink():
            raise ValueError(f"Save cannot be a symbolic link: {name}")
        path.mkdir(parents=False, exist_ok=True)
        return path

    def _write_active_name(self, name: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.active_file.with_name(f"{self.active_file.name}.tmp")
        temporary.write_text(name, encoding="utf-8")
        os.replace(temporary, self.active_file)

    def _describe(self, path: Path) -> SaveInfo:
        try:
            modified = datetime.fromtimestamp(
                path.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat()
        except OSError:
            modified = None
        return SaveInfo(
            name=path.name,
            active=path.name == self.runtime.campaign_id,
            initialized=(path / "core_memory.json").is_file(),
            modified_at=modified,
        )


SAVE_MANAGER = SaveManager()


def list_saves() -> dict[str, Any]:
    return SAVE_MANAGER.payload()


def create_save(payload: dict[str, Any]) -> dict[str, Any]:
    save = SAVE_MANAGER.create(
        str(payload.get("name", "")),
        activate=bool(payload.get("activate", True)),
        discard_pending=bool(payload.get("discard_pending", False)),
    )
    return {"save": save.as_dict(), **SAVE_MANAGER.payload()}


def switch_save(payload: dict[str, Any]) -> dict[str, Any]:
    save = SAVE_MANAGER.switch(
        str(payload.get("name", "")),
        discard_pending=bool(payload.get("discard_pending", False)),
    )
    return {"save": save.as_dict(), **SAVE_MANAGER.payload()}
