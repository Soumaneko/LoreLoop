from dataclasses import dataclass
from pathlib import Path


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


DEFAULT_CAMPAIGN_PATHS = CampaignPaths.from_data_dir("data")

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
