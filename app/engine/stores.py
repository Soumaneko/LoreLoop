from app.domain.core_memory import CoreMemoryStore
from app.domain.current_state import CurrentStateStore
from app.domain.director_event.store import DirectorEventStore
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.entity.store import EntityStore
from app.domain.recall_memory.pending_store import PendingMemoryStore
from app.domain.recall_memory.store import MemoryStore
from app.domain.scene_summary.store import SceneSummaryStore
from app.domain.short_term_memory import ShortTermMemoryStore

from .paths import (
    CORE_MEMORY_JSON,
    DATA_DIR,
    DIRECTOR_EVENT_DB,
    DIRECTOR_NPC_DB,
    ENTITY_DB,
    MEMORY_DB,
    PENDING_MEMORY_DB,
    SCENE_SUMMARY_DB,
    SHORT_TERM_DB,
    STATE_DB,
)


def current_state_store() -> CurrentStateStore:
    DATA_DIR.mkdir(exist_ok=True)
    return CurrentStateStore(str(STATE_DB))


def memory_store() -> MemoryStore:
    DATA_DIR.mkdir(exist_ok=True)
    return MemoryStore(str(MEMORY_DB))


def pending_memory_store() -> PendingMemoryStore:
    DATA_DIR.mkdir(exist_ok=True)
    return PendingMemoryStore(str(PENDING_MEMORY_DB))


def short_term_memory_store() -> ShortTermMemoryStore:
    DATA_DIR.mkdir(exist_ok=True)
    return ShortTermMemoryStore(str(SHORT_TERM_DB), max_items=5)


def scene_summary_store() -> SceneSummaryStore:
    DATA_DIR.mkdir(exist_ok=True)
    return SceneSummaryStore(str(SCENE_SUMMARY_DB))


def entity_store() -> EntityStore:
    DATA_DIR.mkdir(exist_ok=True)
    return EntityStore(str(ENTITY_DB))


def core_memory_store() -> CoreMemoryStore:
    DATA_DIR.mkdir(exist_ok=True)
    return CoreMemoryStore(CORE_MEMORY_JSON)


def director_event_store() -> DirectorEventStore:
    DATA_DIR.mkdir(exist_ok=True)
    return DirectorEventStore(str(DIRECTOR_EVENT_DB))


def director_npc_store() -> DirectorNPCStore:
    DATA_DIR.mkdir(exist_ok=True)
    return DirectorNPCStore(str(DIRECTOR_NPC_DB))
