from dataclasses import dataclass
import os
from pathlib import Path
from uuid import UUID, uuid4

from .app_paths import (
    APP_PATHS,
    ensure_user_directories,
    migrate_legacy_user_data,
)


migrate_legacy_user_data(APP_PATHS)
ensure_user_directories(APP_PATHS)

SAVES_DIR = APP_PATHS.saves_dir
APP_CONFIG_DIR = APP_PATHS.config_dir
LOGS_DIR = APP_PATHS.logs_dir
WEBVIEW_DIR = APP_PATHS.webview_dir
ACTIVE_SAVE_FILE = APP_CONFIG_DIR / "active_save"


def validate_save_name(name: str) -> str:
    clean = str(name).strip()
    if not clean or len(clean) > 80:
        raise ValueError("Save name must contain 1-80 characters")
    if clean in {".", ".."} or clean.startswith("."):
        raise ValueError("Save name cannot be hidden or relative")
    if any(char in clean for char in ("/", "\\", "\0")):
        raise ValueError("Save name cannot contain path separators")
    if any(ord(char) < 32 for char in clean):
        raise ValueError("Save name cannot contain control characters")
    return clean


def configured_save_id() -> str:
    try:
        raw = ACTIVE_SAVE_FILE.read_text(encoding="utf-8").strip()
        save_id = str(UUID(raw))
        if raw == save_id and (SAVES_DIR / save_id).is_dir():
            return save_id
    except (FileNotFoundError, OSError, ValueError):
        pass
    save_id = str(uuid4())
    (SAVES_DIR / save_id).mkdir(parents=False, exist_ok=False)
    temporary = ACTIVE_SAVE_FILE.with_name(f"{ACTIVE_SAVE_FILE.name}.tmp")
    temporary.write_text(save_id, encoding="utf-8")
    os.replace(temporary, ACTIVE_SAVE_FILE)
    return save_id


@dataclass(frozen=True)
class CampaignPaths:
    data_dir: Path

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "CampaignPaths":
        return cls(data_dir=Path(data_dir))

    @property
    def state_db(self) -> Path:
        return self.data_dir / "current_state.db"

    @property
    def memory_db(self) -> Path:
        return self.data_dir / "memories.db"

    @property
    def pending_memory_db(self) -> Path:
        return self.data_dir / "pending_memories.db"

    @property
    def short_term_db(self) -> Path:
        return self.data_dir / "short_term.db"

    @property
    def scene_summary_db(self) -> Path:
        return self.data_dir / "scene_summaries.db"

    @property
    def canon_evidence_db(self) -> Path:
        return self.data_dir / "canon_evidence.db"

    @property
    def entity_db(self) -> Path:
        return self.data_dir / "entities.db"

    @property
    def npc_profile_db(self) -> Path:
        return self.data_dir / "npc_profiles.db"

    @property
    def director_event_db(self) -> Path:
        return self.data_dir / "director_events.db"

    @property
    def director_npc_db(self) -> Path:
        return self.data_dir / "director_npcs.db"

    @property
    def world_setting_db(self) -> Path:
        return self.data_dir / "world_settings.db"

    @property
    def core_memory_json(self) -> Path:
        return self.data_dir / "core_memory.json"

    @property
    def settings_json(self) -> Path:
        return self.data_dir / "settings.json"


DEFAULT_CAMPAIGN_PATHS = CampaignPaths.from_data_dir(
    SAVES_DIR / configured_save_id()
)
