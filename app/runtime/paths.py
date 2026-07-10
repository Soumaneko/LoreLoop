from pathlib import Path


DATA_DIR = Path("data")
STATE_DB = DATA_DIR / "current_state.db"
MEMORY_DB = DATA_DIR / "memories.db"
PENDING_MEMORY_DB = DATA_DIR / "pending_memories.db"
SHORT_TERM_DB = DATA_DIR / "short_term.db"
SCENE_SUMMARY_DB = DATA_DIR / "scene_summaries.db"
ENTITY_DB = DATA_DIR / "entities.db"
NPC_PROFILE_DB = DATA_DIR / "npc_profiles.db"
DIRECTOR_EVENT_DB = DATA_DIR / "director_events.db"
DIRECTOR_NPC_DB = DATA_DIR / "director_npcs.db"
CORE_MEMORY_JSON = DATA_DIR / "core_memory.json"
SETTINGS_JSON = DATA_DIR / "settings.json"
