from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAVES_DIR = PROJECT_ROOT / "saves"
LEGACY_DATA_DIR = PROJECT_ROOT / "data"
LEGACY_APP_SAVES_DIR = PROJECT_ROOT / "app" / "saves"
ACTIVE_SAVE_FILE = SAVES_DIR / ".active_save"
DEFAULT_SAVE_NAME = "default"


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


def configured_save_name() -> str:
    candidates = (
        (ACTIVE_SAVE_FILE, SAVES_DIR),
        (LEGACY_APP_SAVES_DIR / ACTIVE_SAVE_FILE.name, LEGACY_APP_SAVES_DIR),
    )
    for active_file, saves_dir in candidates:
        try:
            name = validate_save_name(active_file.read_text(encoding="utf-8"))
            if (saves_dir / name).is_dir():
                return name
        except (FileNotFoundError, OSError, ValueError):
            continue
    return DEFAULT_SAVE_NAME


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
    def core_memory_json(self) -> Path:
        return self.data_dir / "core_memory.json"

    @property
    def settings_json(self) -> Path:
        return self.data_dir / "settings.json"


DEFAULT_CAMPAIGN_PATHS = CampaignPaths.from_data_dir(
    SAVES_DIR / configured_save_name()
)

# Stable compatibility constants for existing callers.
DATA_DIR = DEFAULT_CAMPAIGN_PATHS.data_dir
STATE_DB = DEFAULT_CAMPAIGN_PATHS.state_db
MEMORY_DB = DEFAULT_CAMPAIGN_PATHS.memory_db
PENDING_MEMORY_DB = DEFAULT_CAMPAIGN_PATHS.pending_memory_db
SHORT_TERM_DB = DEFAULT_CAMPAIGN_PATHS.short_term_db
SCENE_SUMMARY_DB = DEFAULT_CAMPAIGN_PATHS.scene_summary_db
ENTITY_DB = DEFAULT_CAMPAIGN_PATHS.entity_db
NPC_PROFILE_DB = DEFAULT_CAMPAIGN_PATHS.npc_profile_db
DIRECTOR_EVENT_DB = DEFAULT_CAMPAIGN_PATHS.director_event_db
DIRECTOR_NPC_DB = DEFAULT_CAMPAIGN_PATHS.director_npc_db
CORE_MEMORY_JSON = DEFAULT_CAMPAIGN_PATHS.core_memory_json
SETTINGS_JSON = DEFAULT_CAMPAIGN_PATHS.settings_json
