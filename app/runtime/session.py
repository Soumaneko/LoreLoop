import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from app.domain.core_memory import InitConfig
from app.domain.current_state import CurrentState
from app.domain.roll.schemas import Difficulty
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.director_npc.schemas import DirectorNPCWorkflowResult
from app.domain.short_term_memory import ShortTermMemory
from app.domain.plot_outline.schemas import PlotOutlineContext
from app.core.llm.config import SharedGatewayFactory
from app.runtime.paths import CampaignPaths, DEFAULT_CAMPAIGN_PATHS
from app.runtime.services import RuntimeServices
from app.runtime.stores import StoreBundle
from app.runtime.jobs import MaintenanceJobCoordinator


@dataclass
class PendingTurn:
    turn_id: str
    group_id: str
    version_index: int
    user_input: str
    difficulty: Difficulty
    max_chars: int
    state_before: CurrentState
    context: PlotOutlineContext
    outline: PlotOutlineResult
    scene_result: SceneWriterResult
    director_npc_result: DirectorNPCWorkflowResult | None = None
    turn_index: int = 1
    short_term_memory: ShortTermMemory | None = None
    confirmation_cache: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingOpening:
    turn_id: str
    config: InitConfig
    current_state: CurrentState
    scene_result: SceneWriterResult
    confirmation_cache: dict[str, Any] = field(default_factory=dict)


class PendingTurnRepository:
    """Thread-safe owner of pending generation variants for one campaign."""

    def __init__(self) -> None:
        self._items: dict[str, PendingTurn] = {}
        self._lock = threading.RLock()

    @property
    def mapping(self) -> dict[str, PendingTurn]:
        """Compatibility view for existing API and debug callers."""
        return self._items

    def get(self, turn_id: str) -> PendingTurn | None:
        with self._lock:
            return self._items.get(turn_id)

    def add(self, pending: PendingTurn) -> None:
        with self._lock:
            self._items[pending.turn_id] = pending

    def variants(self, group_id: str) -> list[PendingTurn]:
        with self._lock:
            return sorted(
                (
                    pending
                    for pending in self._items.values()
                    if pending.group_id == group_id
                ),
                key=lambda pending: pending.version_index,
            )

    def discard_group(self, group_id: str) -> None:
        with self._lock:
            turn_ids = [
                pending.turn_id
                for pending in self._items.values()
                if pending.group_id == group_id
            ]
            for turn_id in turn_ids:
                self._items.pop(turn_id, None)


class PendingOpeningRepository:
    """Thread-safe owner of pending openings for one campaign."""

    def __init__(self) -> None:
        self._items: dict[str, PendingOpening] = {}
        self._lock = threading.RLock()

    @property
    def mapping(self) -> dict[str, PendingOpening]:
        return self._items

    def get(self, turn_id: str) -> PendingOpening | None:
        with self._lock:
            return self._items.get(turn_id)

    def add(self, pending: PendingOpening) -> None:
        with self._lock:
            self._items[pending.turn_id] = pending

    def remove(self, turn_id: str) -> None:
        with self._lock:
            self._items.pop(turn_id, None)


class CampaignRuntime:
    """Mutable runtime resources scoped to one campaign."""

    def __init__(
        self,
        campaign_id: str = "default",
        *,
        paths: CampaignPaths | None = None,
        stores: StoreBundle | None = None,
        gateway_factory: Callable | None = None,
        max_workers: int = 2,
    ) -> None:
        self.campaign_id = campaign_id
        self.paths = paths or DEFAULT_CAMPAIGN_PATHS
        self.stores = stores or StoreBundle(self.paths)
        self.gateway_factory = gateway_factory or SharedGatewayFactory()
        self.pending_turns = PendingTurnRepository()
        self.pending_openings = PendingOpeningRepository()
        self.jobs = MaintenanceJobCoordinator(max_workers=max_workers)
        self.services = RuntimeServices(
            stores=self.stores,
            gateway_factory=self.gateway_factory,
            jobs=self.jobs,
        )
        self._confirmation_lock = threading.Lock()

    @property
    def confirmation_lock(self) -> threading.Lock:
        """Serialize commits inside this campaign while allowing other runtimes."""
        return self._confirmation_lock

    def close(self, *, wait_for_jobs: bool = True) -> None:
        self.jobs.shutdown(wait=wait_for_jobs)


DEFAULT_CAMPAIGN_RUNTIME = CampaignRuntime()

# Compatibility aliases for the stable API and existing local tooling. New code
# should access these collections through get_campaign_runtime().
PENDING_TURNS = DEFAULT_CAMPAIGN_RUNTIME.pending_turns.mapping
PENDING_OPENINGS = DEFAULT_CAMPAIGN_RUNTIME.pending_openings.mapping
BACKGROUND_EXECUTOR = DEFAULT_CAMPAIGN_RUNTIME.jobs.executor


def get_campaign_runtime() -> CampaignRuntime:
    return DEFAULT_CAMPAIGN_RUNTIME


def pending_turn_variants(group_id: str) -> list[PendingTurn]:
    return get_campaign_runtime().pending_turns.variants(group_id)


def discard_pending_turn_group(group_id: str) -> None:
    get_campaign_runtime().pending_turns.discard_group(group_id)


def confirmation_lock(turn_id: str) -> threading.Lock:
    """Compatibility wrapper; confirmation is serialized per campaign runtime."""
    return get_campaign_runtime().confirmation_lock


def submit_memory_batch_once(fn, /, *args, **kwargs) -> bool:
    return get_campaign_runtime().jobs.submit_memory_batch_once(fn, *args, **kwargs)
