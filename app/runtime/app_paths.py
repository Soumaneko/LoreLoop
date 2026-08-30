from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from uuid import UUID

from platformdirs import user_data_path

from app.runtime.environment import load_source_dotenv

APP_NAME = "LoreLoop"
DATA_DIR_ENV = "LORELOOP_DATA_DIR"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

load_source_dotenv()

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
        )
        if not directory.is_dir()
    ]
    if missing:
        raise RuntimeError(
            "LoreLoop read-only resources are missing: " + ", ".join(missing)
        )

def validate_debug_resource_paths(paths: ApplicationPaths) -> None:
    if not paths.debug_static_dir.is_dir():
        raise RuntimeError(
            "LoreLoop debug resources are missing: "
            + str(paths.debug_static_dir)
        )


def _is_save_id(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


APP_PATHS = resolve_application_paths()
validate_resource_paths(APP_PATHS)
