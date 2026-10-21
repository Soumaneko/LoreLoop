from app.core.client_error import ClientValidationError
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

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
    policy: Literal["recent_then_new", "new", "recent"] = "recent_then_new",
) -> str:
    """Resolve which save is active, keeping the active file in sync.

    Priority: the persisted active save, then the most recently played save,
    then a freshly minted UUID save (when the policy allows). The active file
    is only rewritten when the persisted value differs from the resolution.
    """
    if root == SAVES_DIR and active_file == ACTIVE_SAVE_FILE:
        ensure_user_directories(APP_PATHS)
    active = _read_active_save_id(root, active_file)
    if active is not None:
        return active
    if policy in ("recent_then_new", "recent"):
        recent = _recent_save_id(root)
        if recent is not None:
            _persist_active_save(active_file, recent)
            return recent
    if policy in ("recent_then_new", "new"):
        save_id = str(uuid4())
        (root / save_id).mkdir(parents=False, exist_ok=False)
        _persist_active_save(active_file, save_id)
        return save_id
    raise RuntimeError("No local save is available")


def _read_active_save_id(root: Path, active_file: Path) -> str | None:
    try:
        raw = active_file.read_text(encoding="utf-8").strip()
        save_id = str(UUID(raw))
        if raw == save_id and (root / save_id).is_dir():
            manifest_id = _manifest_save_id(root / save_id)
            if manifest_id is None or manifest_id == save_id:
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
    except (FileNotFoundError, OSError, ValueError):
        return None


def _recent_save_id(root: Path) -> str | None:
    best: tuple[str, str] | None = None
    for child in root.iterdir():
        if (
            not child.is_dir()
            or not _is_save_id(child.name)
            or child.name.startswith(_STAGING_DIR_PREFIXES)
        ):
            continue
        try:
            data = json.loads(
                (child / "save_manifest.json").read_text(encoding="utf-8")
            )
            last_played = str(data.get("last_played_at") or "")
        except (OSError, ValueError):
            last_played = ""
        if best is None or last_played > best[0]:
            best = (last_played, child.name)
    return best[1] if best is not None else None


def _persist_active_save(active_file: Path, save_id: str) -> None:
    try:
        if active_file.read_text(encoding="utf-8").strip() == save_id:
            return
    except (FileNotFoundError, OSError):
        pass
    temporary = active_file.with_name(f"{active_file.name}.tmp")
    temporary.write_text(save_id, encoding="utf-8")
    os.replace(temporary, active_file)


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


def default_campaign_paths() -> CampaignPaths:
    return CampaignPaths.from_data_dir(SAVES_DIR / resolve_active_save_id())
