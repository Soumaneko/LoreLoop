from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from app.core.time import utc_datetime_now
from .paths import validate_save_name


SAVE_FORMAT_VERSION = 1
SAVE_MANIFEST_FILE = "save_manifest.json"


_utc_now = utc_datetime_now


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Save manifest timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _validate_timestamp(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Save manifest {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Save manifest {field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Save manifest {field} must include a timezone")
    return _timestamp(parsed)


@dataclass(frozen=True)
class SaveManifest:
    """Stable identity and user-editable label for one local save."""

    save_id: str
    display_name: str
    created_at: str
    last_played_at: str
    format_version: int = SAVE_FORMAT_VERSION

    @classmethod
    def from_dict(cls, value: object) -> "SaveManifest":
        if not isinstance(value, dict):
            raise ValueError("Save manifest must be a JSON object")
        if value.get("format_version") != SAVE_FORMAT_VERSION:
            raise ValueError(
                f"Unsupported save format version: {value.get('format_version')!r}"
            )
        raw_save_id = value.get("save_id")
        if not isinstance(raw_save_id, str):
            raise ValueError("Save manifest save_id must be a UUID")
        try:
            save_id = str(UUID(raw_save_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Save manifest save_id must be a UUID") from exc
        if raw_save_id != save_id:
            raise ValueError("Save manifest save_id must use canonical UUID syntax")

        return cls(
            save_id=save_id,
            display_name=validate_save_name(value.get("display_name", "")),
            created_at=_validate_timestamp(value.get("created_at"), "created_at"),
            last_played_at=_validate_timestamp(
                value.get("last_played_at"), "last_played_at"
            ),
            format_version=SAVE_FORMAT_VERSION,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SaveMetadata:
    protagonist_name: str | None = None
    location: str | None = None
    turn_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SaveManifestRepository:
    """Read and atomically update a manifest within an already chosen save."""

    def __init__(
        self,
        save_dir: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.save_dir = Path(save_dir)
        self.path = self.save_dir / SAVE_MANIFEST_FILE
        self._clock = clock

    def create(
        self,
        display_name: str,
        *,
        save_id: str | None = None,
        created_at: datetime | None = None,
        last_played_at: datetime | None = None,
    ) -> SaveManifest:
        if self.path.exists():
            raise FileExistsError(f"Save manifest already exists: {self.path}")
        created = created_at or self._clock()
        manifest = SaveManifest.from_dict(
            {
                "format_version": SAVE_FORMAT_VERSION,
                "save_id": save_id or str(uuid4()),
                "display_name": display_name,
                "created_at": _timestamp(created),
                "last_played_at": _timestamp(last_played_at or created),
            }
        )
        self._write(manifest)
        return manifest

    def load(self) -> SaveManifest:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid save manifest JSON: {self.path}") from exc
        return SaveManifest.from_dict(value)

    def rename(self, display_name: str) -> SaveManifest:
        manifest = replace(
            self.load(),
            display_name=validate_save_name(display_name),
        )
        self._write(manifest)
        return manifest

    def touch(self, *, played_at: datetime | None = None) -> SaveManifest:
        manifest = replace(
            self.load(),
            last_played_at=_timestamp(played_at or self._clock()),
        )
        self._write(manifest)
        return manifest

    def _write(self, manifest: SaveManifest) -> None:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            manifest.as_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.save_dir,
                prefix=f".{SAVE_MANIFEST_FILE}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def load_save_metadata(save_dir: str | Path) -> SaveMetadata:
    """Project list metadata without creating or migrating either database."""

    data_dir = Path(save_dir)
    protagonist_name, location = _read_current_state_metadata(
        data_dir / "current_state.db"
    )
    turn_count = _read_turn_count(data_dir / "short_term.db")
    return SaveMetadata(
        protagonist_name=protagonist_name,
        location=location,
        turn_count=turn_count,
    )


def _connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=1,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _read_current_state_metadata(path: Path) -> tuple[str | None, str | None]:
    try:
        with closing(_connect_read_only(path)) as connection:
            row = connection.execute(
                "SELECT state_json FROM current_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return None, None
        state = json.loads(row["state_json"])
        if not isinstance(state, dict):
            return None, None
        player = state.get("player")
        scene = state.get("scene")
        protagonist = player.get("name") if isinstance(player, dict) else None
        location = scene.get("location") if isinstance(scene, dict) else None
        return _optional_text(protagonist), _optional_text(location)
    except (FileNotFoundError, OSError, sqlite3.Error, json.JSONDecodeError, TypeError):
        return None, None


def _read_turn_count(path: Path) -> int:
    try:
        with closing(_connect_read_only(path)) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS turn_count FROM short_term_scenes"
            ).fetchone()
        return max(0, int(row["turn_count"])) if row is not None else 0
    except (FileNotFoundError, OSError, sqlite3.Error, TypeError, ValueError):
        return 0


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean or None
