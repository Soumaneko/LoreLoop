from dataclasses import dataclass

from app.domain.core_memory import CoreMemoryStore
from app.domain.current_state import CurrentStateStore
from app.domain.director_event.store import DirectorEventStore
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.entity.store import EntityStore
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.recall_memory.pending_store import PendingMemoryStore
from app.domain.recall_memory.store import MemoryStore
from app.domain.scene_summary.store import SceneSummaryStore
from app.domain.short_term_memory import ShortTermMemoryStore

from .paths import CampaignPaths, DEFAULT_CAMPAIGN_PATHS


@dataclass(frozen=True)
class StoreBundle:
    """Store factory collection scoped to one campaign data directory."""

    paths: CampaignPaths

    def _ensure_data_dir(self) -> None:
        self.paths.data_dir.mkdir(parents=True, exist_ok=True)

    def current_state(self) -> CurrentStateStore:
        self._ensure_data_dir()
        return CurrentStateStore(str(self.paths.state_db))

    def memory(self) -> MemoryStore:
        self._ensure_data_dir()
        return MemoryStore(str(self.paths.memory_db))

    def pending_memory(self) -> PendingMemoryStore:
        self._ensure_data_dir()
        return PendingMemoryStore(str(self.paths.pending_memory_db))

    def short_term(self) -> ShortTermMemoryStore:
        self._ensure_data_dir()
        return ShortTermMemoryStore(str(self.paths.short_term_db), max_items=5)

    def scene_summary(self) -> SceneSummaryStore:
        self._ensure_data_dir()
        return SceneSummaryStore(str(self.paths.scene_summary_db))

    def entity(self) -> EntityStore:
        self._ensure_data_dir()
        return EntityStore(str(self.paths.entity_db))

    def npc_profile(self) -> NPCProfileStore:
        self._ensure_data_dir()
        return NPCProfileStore(str(self.paths.npc_profile_db))

    def core_memory(self) -> CoreMemoryStore:
        self._ensure_data_dir()
        return CoreMemoryStore(self.paths.core_memory_json)

    def director_event(self) -> DirectorEventStore:
        self._ensure_data_dir()
        return DirectorEventStore(str(self.paths.director_event_db))

    def director_npc(self) -> DirectorNPCStore:
        self._ensure_data_dir()
        return DirectorNPCStore(str(self.paths.director_npc_db))


DEFAULT_STORE_BUNDLE = StoreBundle(DEFAULT_CAMPAIGN_PATHS)


def _active_stores() -> StoreBundle:
    from .session import get_campaign_runtime

    return get_campaign_runtime().stores


def current_state_store() -> CurrentStateStore:
    return _active_stores().current_state()


def memory_store() -> MemoryStore:
    return _active_stores().memory()


def pending_memory_store() -> PendingMemoryStore:
    return _active_stores().pending_memory()


def short_term_memory_store() -> ShortTermMemoryStore:
    return _active_stores().short_term()


def scene_summary_store() -> SceneSummaryStore:
    return _active_stores().scene_summary()


def entity_store() -> EntityStore:
    return _active_stores().entity()


def npc_profile_store() -> NPCProfileStore:
    return _active_stores().npc_profile()


def core_memory_store() -> CoreMemoryStore:
    return _active_stores().core_memory()


def director_event_store() -> DirectorEventStore:
    return _active_stores().director_event()


def director_npc_store() -> DirectorNPCStore:
    return _active_stores().director_npc()
from dataclasses import dataclass
