from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from uuid import UUID

from platformdirs import user_data_path
from dotenv import load_dotenv


APP_NAME = "LoreLoop"
DATA_DIR_ENV = "LORELOOP_DATA_DIR"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LEGACY_PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv()


@dataclass(frozen=True)
class ApplicationPaths:
    data_root: Path
    saves_dir: Path
    config_dir: Path
    logs_dir: Path
    debug_prompts_dir: Path
    webview_dir: Path
    prompts_dir: Path
    player_static_dir: Path
    debug_static_dir: Path
    data_dir_overridden: bool = False


def resolve_data_root(
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, bool]:
    values = os.environ if environ is None else environ
    override = str(values.get(DATA_DIR_ENV, "")).strip()
    if override:
        path = Path(os.path.expandvars(override)).expanduser()
        if not path.is_absolute():
            raise RuntimeError(f"{DATA_DIR_ENV} must be an absolute path")
        return path.resolve(), True
    return (
        Path(
            user_data_path(
                appname=APP_NAME,
                appauthor=False,
                roaming=False,
            )
        ),
        False,
    )


def resolve_application_paths(
    environ: Mapping[str, str] | None = None,
) -> ApplicationPaths:
    data_root, overridden = resolve_data_root(environ)
    return ApplicationPaths(
        data_root=data_root,
        saves_dir=data_root / "saves",
        config_dir=data_root / "config",
        logs_dir=data_root / "logs",
        debug_prompts_dir=data_root / "debug_prompts",
        webview_dir=data_root / "webview",
        prompts_dir=PACKAGE_ROOT / "prompts",
        player_static_dir=PACKAGE_ROOT / "player_static",
        debug_static_dir=PACKAGE_ROOT / "debug_static",
        data_dir_overridden=overridden,
    )


def migrate_legacy_user_data(
    paths: ApplicationPaths,
    *,
    legacy_project_root: str | Path = LEGACY_PROJECT_ROOT,
    frozen: bool | None = None,
) -> str:
    """Copy the pre-desktop source layout once, without deleting the source."""
    if paths.data_dir_overridden:
        return "skipped_override"
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return "skipped_frozen"

    data_root = paths.data_root
    if data_root.exists() or data_root.is_symlink():
        return "skipped_destination_exists"

    legacy_root = Path(legacy_project_root)
    legacy_saves = legacy_root / "saves"
    legacy_config = legacy_saves / ".app"
    legacy_logs = legacy_root / "logs"
    if not legacy_saves.is_dir() and not legacy_config.is_dir():
        return "no_legacy_data"

    data_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{data_root.name}-migration-",
            dir=data_root.parent,
        )
    )
    copied = False
    try:
        stage_saves = stage / "saves"
        if legacy_saves.is_dir():
            for source in legacy_saves.iterdir():
                if not source.is_dir() or not _is_save_id(source.name):
                    continue
                shutil.copytree(source, stage_saves / source.name)
                copied = True

        stage_config = stage / "config"
        for filename in ("model_config.json", "reader_preferences.json"):
            source = legacy_config / filename
            if source.is_file():
                stage_config.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, stage_config / filename)
                copied = True

        active_save = _valid_legacy_active_save(legacy_saves, stage_saves)
        if active_save is not None:
            stage_config.mkdir(parents=True, exist_ok=True)
            (stage_config / "active_save").write_text(
                active_save,
                encoding="utf-8",
            )
            copied = True

        if legacy_logs.is_dir():
            for source in legacy_logs.glob("*.jsonl"):
                try:
                    (stage / "logs").mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, stage / "logs" / source.name)
                except OSError:
                    continue

        if not copied:
            return "no_legacy_data"

        stage_config.mkdir(parents=True, exist_ok=True)
        (stage_config / "migration-v1.json").write_text(
            json.dumps(
                {
                    "source": str(legacy_root),
                    "completed_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            os.replace(stage, data_root)
        except OSError:
            if data_root.exists() or data_root.is_symlink():
                return "skipped_destination_race"
            raise
        return "migrated"
    except Exception as exc:
        raise RuntimeError(
            f"Failed to copy legacy LoreLoop data from {legacy_root}"
        ) from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def ensure_user_directories(paths: ApplicationPaths) -> None:
    for directory in (
        paths.saves_dir,
        paths.config_dir,
        paths.logs_dir,
        paths.webview_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass


def validate_resource_paths(paths: ApplicationPaths) -> None:
    missing = [
        str(directory)
        for directory in (
            paths.prompts_dir,
            paths.player_static_dir,
            paths.debug_static_dir,
        )
        if not directory.is_dir()
    ]
    if missing:
        raise RuntimeError(
            "LoreLoop read-only resources are missing: " + ", ".join(missing)
        )


def _valid_legacy_active_save(
    legacy_saves: Path,
    staged_saves: Path,
) -> str | None:
    try:
        raw = (legacy_saves / ".active_save").read_text(encoding="utf-8").strip()
        save_id = str(UUID(raw))
    except (FileNotFoundError, OSError, ValueError):
        return None
    if raw != save_id or not (staged_saves / save_id).is_dir():
        return None
    return save_id


def _is_save_id(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


APP_PATHS = resolve_application_paths()
validate_resource_paths(APP_PATHS)
