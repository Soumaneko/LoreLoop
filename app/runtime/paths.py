from app.core.client_error import ClientValidationError
from dataclasses import dataclass
import json
from pathlib import Path
from uuid import UUID

from .app_paths import APP_PATHS, _is_save_id, ensure_user_directories

SAVES_DIR = APP_PATHS.saves_dir
APP_CONFIG_DIR = APP_PATHS.config_dir
LOGS_DIR = APP_PATHS.logs_dir
WEBVIEW_DIR = APP_PATHS.webview_dir
ACTIVE_SAVE_FILE = APP_CONFIG_DIR / "active_save"

_STAGING_DIR_PREFIXES = (".copy-", ".import-", ".trash-")


def validate_save_name(name: str) -> str:
    clean = str(name).strip()
    if not clean or len(clean) > 80:
        raise ClientValidationError("存档名称长度需在 1-80 个字符之间：请修改名称。")
    if clean in {".", ".."} or clean.startswith("."):
        raise ClientValidationError("存档名称不能为隐藏名称或相对路径：请修改名称。")
    if any(char in clean for char in ("/", "\\", "\0")):
        raise ClientValidationError("存档名称不能包含路径分隔符（/ 或 \\）：请修改名称。")
    if any(ord(char) < 32 for char in clean):
        raise ClientValidationError("存档名称不能包含控制字符：请修改名称。")
    return clean


def resolve_active_save_id(
    *,
    root: Path = SAVES_DIR,
    active_file: Path = ACTIVE_SAVE_FILE,
) -> str | None:
    """Find the persisted active save or the most recently played save."""
    if root == SAVES_DIR and active_file == ACTIVE_SAVE_FILE:
        ensure_user_directories(APP_PATHS)
    active = _read_active_save_id(root, active_file)
    if active is not None:
        return active
    return _recent_save_id(root)


def _read_active_save_id(root: Path, active_file: Path) -> str | None:
    try:
        raw = active_file.read_text(encoding="utf-8").strip()
        save_id = str(UUID(raw))
        path = root / save_id
        if raw == save_id and path.is_dir() and not path.is_symlink():
            if _manifest_save_id(path) == save_id:
                return save_id
    except (FileNotFoundError, OSError, ValueError):
        pass
    return None


def _manifest_save_id(save_dir: Path) -> str | None:
    try:
        data = json.loads(
            (save_dir / "save_manifest.json").read_text(encoding="utf-8")
        )
        raw = str(data.get("save_id") or "")
        save_id = str(UUID(raw))
        if raw != save_id:
            return None
        return save_id
    except (OSError, ValueError, AttributeError):
        return None


def _recent_save_id(root: Path) -> str | None:
    best: tuple[str, str] | None = None
    for child in root.iterdir():
        if (
            not child.is_dir()
            or child.is_symlink()
            or not _is_save_id(child.name)
            or child.name.startswith(_STAGING_DIR_PREFIXES)
        ):
            continue
        try:
            data = json.loads(
                (child / "save_manifest.json").read_text(encoding="utf-8")
            )
            last_played = str(data.get("last_played_at") or "")
            if data.get("save_id") != child.name:
                continue
        except (OSError, ValueError, AttributeError):
            continue
        if best is None or last_played > best[0]:
            best = (last_played, child.name)
    return best[1] if best is not None else None


@dataclass(frozen=True)
class CampaignPaths:
    data_dir: Path

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "CampaignPaths":
        return cls(data_dir=Path(data_dir))

    @property
    def campaign_db(self) -> Path:
        return self.data_dir / "campaign.db"

    @property
    def control_db(self) -> Path:
        return self.data_dir / ".runtime" / "control.db"

    @property
    def core_memory_json(self) -> Path:
        return self.data_dir / "core_memory.json"

    @property
    def settings_json(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def initialization_draft_json(self) -> Path:
        return self.data_dir / "initialization_draft.json"

    @property
    def initialization_json(self) -> Path:
        return self.data_dir / "initialization.json"


def default_campaign_paths() -> CampaignPaths | None:
    save_id = resolve_active_save_id()
    return CampaignPaths.from_data_dir(SAVES_DIR / save_id) if save_id else None
