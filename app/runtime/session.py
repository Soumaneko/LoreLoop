import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from app.domain.core_memory import InitConfig
from app.domain.current_state import CurrentState
from app.domain.roll.schemas import Difficulty
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.director_npc.schemas import DirectorNPCWorkflowResult
from app.domain.short_term_memory import ShortTermMemory
from app.core.llm.config import SharedGatewayFactory
from app.runtime.paths import (
    CampaignPaths,
    DEFAULT_CAMPAIGN_PATHS,
    configured_save_name,
)
from app.runtime.services import RuntimeServices
from app.runtime.stores import StoreBundle
from app.runtime.jobs import MaintenanceJobCoordinator
from app.runtime.operations import OperationManager
from app.runtime.serialization import model_data
from app.runtime.turn_history import CampaignHistory


@dataclass
class PendingTurn:
    turn_id: str
    group_id: str
    version_index: int
    user_input: str
    difficulty: Difficulty
    max_chars: int
    state_before: CurrentState
    context: NarrativeComposerContext
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
        self._on_add = None
        self._on_remove = None

    def configure_callbacks(self, *, on_add=None, on_remove=None) -> None:
        self._on_add = on_add
        self._on_remove = on_remove

    def get(self, turn_id: str) -> PendingTurn | None:
        with self._lock:
            return self._items.get(turn_id)

    def __contains__(self, turn_id: str) -> bool:
        return self.get(turn_id) is not None

    def add(self, pending: PendingTurn) -> None:
        with self._lock:
            self._items[pending.turn_id] = pending
        if self._on_add:
            self._on_add(pending)

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

    def values(self) -> list[PendingTurn]:
        with self._lock:
            return list(self._items.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def discard_group(self, group_id: str) -> None:
        with self._lock:
            turn_ids = [
                pending.turn_id
                for pending in self._items.values()
                if pending.group_id == group_id
            ]
            for turn_id in turn_ids:
                self._items.pop(turn_id, None)
        if self._on_remove:
            self._on_remove(turn_ids)

    def remove(self, turn_id: str) -> None:
        with self._lock:
            removed = self._items.pop(turn_id, None)
        if removed is not None and self._on_remove:
            self._on_remove([turn_id])

    def restore(self, pending: PendingTurn) -> None:
        with self._lock:
            self._items[pending.turn_id] = pending

    def clear(self, *, discard: bool = False) -> None:
        with self._lock:
            turn_ids = list(self._items)
            self._items.clear()
        if discard and self._on_remove:
            self._on_remove(turn_ids)


class PendingOpeningRepository:
    """Thread-safe owner of pending openings for one campaign."""

    def __init__(self) -> None:
        self._items: dict[str, PendingOpening] = {}
        self._lock = threading.RLock()
        self._on_add = None
        self._on_remove = None

    def configure_callbacks(self, *, on_add=None, on_remove=None) -> None:
        self._on_add = on_add
        self._on_remove = on_remove

    def get(self, turn_id: str) -> PendingOpening | None:
        with self._lock:
            return self._items.get(turn_id)

    def __contains__(self, turn_id: str) -> bool:
        return self.get(turn_id) is not None

    def add(self, pending: PendingOpening) -> None:
        with self._lock:
            self._items[pending.turn_id] = pending
        if self._on_add:
            self._on_add(pending)

    def remove(self, turn_id: str) -> None:
        with self._lock:
            self._items.pop(turn_id, None)
        if self._on_remove:
            self._on_remove([turn_id])

    def restore(self, pending: PendingOpening) -> None:
        with self._lock:
            self._items[pending.turn_id] = pending

    def values(self) -> list[PendingOpening]:
        with self._lock:
            return list(self._items.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def clear(self, *, discard: bool = False) -> None:
        with self._lock:
            turn_ids = list(self._items)
            self._items.clear()
        if discard and self._on_remove:
            self._on_remove(turn_ids)


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
        self._max_workers = max_workers
        self.pending_turns = PendingTurnRepository()
        self.pending_openings = PendingOpeningRepository()
        self.operations = OperationManager(campaign_id, max_workers=max_workers)
        self.jobs = MaintenanceJobCoordinator(
            max_workers=max_workers,
            operation_manager=self.operations,
        )
        self.services = RuntimeServices(
            stores=self.stores,
            gateway_factory=self.gateway_factory,
            jobs=self.jobs,
        )
        self._confirmation_lock = threading.RLock()
        self.history = CampaignHistory(self.paths.data_dir)
        self.history.recover_interrupted()
        self._configure_pending_persistence()
        self._restore_pending_transactions()

    @property
    def confirmation_lock(self) -> threading.RLock:
        """Serialize commits inside this campaign while allowing other runtimes."""
        return self._confirmation_lock

    def has_pending_content(self) -> bool:
        return bool(len(self.pending_turns) or len(self.pending_openings))

    def has_active_operations(self) -> bool:
        return bool(self.operations.list(active_only=True, limit=1))

    def discard_pending_turn(self, turn_id: str) -> dict[str, Any]:
        with self._confirmation_lock:
            pending = self.pending_turns.get(turn_id)
            if pending is None:
                raise ValueError("Pending turn not found")
            variants = self.pending_turns.variants(pending.group_id)
            discarded_turn_ids = [variant.turn_id for variant in variants]
            self.pending_turns.discard_group(pending.group_id)
            return {
                "group_id": pending.group_id,
                "user_input": pending.user_input,
                "difficulty": pending.difficulty,
                "max_chars": pending.max_chars,
                "discarded_turn_ids": discarded_turn_ids,
            }

    def _configure_pending_persistence(self) -> None:
        self.pending_turns.configure_callbacks(
            on_add=self._persist_pending_turn,
            on_remove=self.history.discard,
        )
        self.pending_openings.configure_callbacks(
            on_add=self._persist_pending_opening,
            on_remove=self.history.discard,
        )

    def _persist_pending_turn(self, pending: PendingTurn) -> bool:
        # Some unit-level callers intentionally use lightweight stand-ins for
        # graph inputs.  Only durable, schema-backed pending turns can safely be
        # reconstructed after a process restart.
        if not (
            isinstance(pending, PendingTurn)
            and isinstance(pending.state_before, CurrentState)
            and isinstance(pending.context, NarrativeComposerContext)
            and isinstance(pending.outline, PlotOutlineResult)
            and isinstance(pending.scene_result, SceneWriterResult)
        ):
            return False
        payload = {
            "turn_id": pending.turn_id,
            "group_id": pending.group_id,
            "version_index": pending.version_index,
            "user_input": pending.user_input,
            "difficulty": model_data(pending.difficulty),
            "max_chars": pending.max_chars,
            "state_before": model_data(pending.state_before),
            "context": model_data(pending.context),
            "outline": model_data(pending.outline),
            "scene_result": model_data(pending.scene_result),
            "director_npc_result": model_data(pending.director_npc_result),
            "turn_index": pending.turn_index,
            "short_term_memory": model_data(pending.short_term_memory),
            # Futures and locks in the in-process cache are deliberately not
            # durable. Confirmation will rebuild these values after restart.
            "confirmation_cache": {},
        }
        self.history.save_pending(
            turn_id=pending.turn_id,
            kind="turn",
            group_id=pending.group_id,
            turn_index=pending.turn_index,
            payload=payload,
        )
        return True

    def _persist_pending_opening(self, pending: PendingOpening) -> bool:
        if not (
            isinstance(pending, PendingOpening)
            and isinstance(pending.config, InitConfig)
            and isinstance(pending.current_state, CurrentState)
            and isinstance(pending.scene_result, SceneWriterResult)
        ):
            return False
        payload = {
            "turn_id": pending.turn_id,
            "config": model_data(pending.config),
            "current_state": model_data(pending.current_state),
            "scene_result": model_data(pending.scene_result),
            "confirmation_cache": {},
        }
        self.history.save_pending(
            turn_id=pending.turn_id,
            kind="opening",
            group_id=None,
            turn_index=0,
            payload=payload,
        )
        return True

    def _restore_pending_transactions(self) -> None:
        for record in self.history.pending_records():
            payload = record["payload_json"]
            import json

            data = json.loads(payload)
            if record.get("scene_text"):
                data["scene_result"]["scene_text"] = record["scene_text"]
            if record["kind"] == "turn":
                self.pending_turns.restore(PendingTurn(
                    turn_id=data["turn_id"],
                    group_id=data["group_id"],
                    version_index=data["version_index"],
                    user_input=data["user_input"],
                    difficulty=data["difficulty"],
                    max_chars=data["max_chars"],
                    state_before=CurrentState.model_validate(data["state_before"]),
                    context=NarrativeComposerContext.model_validate(data["context"]),
                    outline=PlotOutlineResult.model_validate(data["outline"]),
                    scene_result=SceneWriterResult.model_validate(data["scene_result"]),
                    director_npc_result=(
                        DirectorNPCWorkflowResult.model_validate(data["director_npc_result"])
                        if data.get("director_npc_result") else None
                    ),
                    turn_index=data["turn_index"],
                    short_term_memory=(
                        ShortTermMemory.model_validate(data["short_term_memory"])
                        if data.get("short_term_memory") else None
                    ),
                ))
            else:
                self.pending_openings.restore(PendingOpening(
                    turn_id=data["turn_id"],
                    config=InitConfig.model_validate(data["config"]),
                    current_state=CurrentState.model_validate(data["current_state"]),
                    scene_result=SceneWriterResult.model_validate(data["scene_result"]),
                ))

    def begin_confirmation(
        self,
        pending: PendingTurn | PendingOpening,
        *,
        write_db: bool,
        scene_text: str | None,
    ) -> bool:
        if not write_db:
            return False
        if isinstance(pending, PendingTurn):
            journaled = self._persist_pending_turn(pending)
        else:
            journaled = self._persist_pending_opening(pending)
        if not journaled:
            return False
        self.jobs.wait_for_idle()
        self.history.begin_confirmation(
            pending.turn_id,
            write_db=write_db,
            scene_text=scene_text,
        )
        return True

    def complete_confirmation(self, pending: PendingTurn | PendingOpening) -> None:
        group_id = pending.group_id if isinstance(pending, PendingTurn) else None
        self.history.complete_confirmation(pending.turn_id, group_id)

    def undo(self) -> dict[str, Any]:
        with self._confirmation_lock:
            if self.has_pending_content():
                raise RuntimeError("Confirm or discard pending content before undo")
            self.jobs.wait_for_idle()
            result = self.history.undo()
            self._restore_pending_transactions()
            self._restart_jobs()
            return result

    def redo(self) -> dict[str, Any]:
        with self._confirmation_lock:
            restored_ids = set(self.history.redo_pending_turn_ids())
            pending_ids = {
                *(pending.turn_id for pending in self.pending_turns.values()),
                *(pending.turn_id for pending in self.pending_openings.values()),
            }
            if pending_ids and pending_ids != restored_ids:
                raise RuntimeError("Confirm or discard pending content before redo")
            self.jobs.wait_for_idle()
            result = self.history.redo()
            self.pending_turns.clear(discard=False)
            self.pending_openings.clear(discard=False)
            self._restart_jobs()
            return result

    def _restart_jobs(self) -> None:
        self.jobs.shutdown(wait=True)
        self.jobs = MaintenanceJobCoordinator(
            max_workers=self._max_workers,
            operation_manager=self.operations,
        )
        self.services = RuntimeServices(
            stores=self.stores,
            gateway_factory=self.gateway_factory,
            jobs=self.jobs,
        )

    def reconfigure(
        self,
        campaign_id: str,
        paths: CampaignPaths,
        *,
        discard_pending: bool = False,
    ) -> None:
        """Switch this runtime to another persisted campaign directory."""
        with self._confirmation_lock:
            if self.has_active_operations():
                raise RuntimeError(
                    "Current save has an active operation. Wait for it to finish "
                    "before switching saves."
                )
            if self.has_pending_content() and not discard_pending:
                raise RuntimeError(
                    "Current save has pending content. Confirm it or explicitly "
                    "discard pending content before switching saves."
                )
            self.operations.shutdown(wait=True)
            self.jobs.shutdown(wait=True)
            self.pending_turns.clear(discard=discard_pending)
            self.pending_openings.clear(discard=discard_pending)
            self.campaign_id = campaign_id
            self.paths = paths
            self.stores = StoreBundle(paths)
            self.operations = OperationManager(
                campaign_id,
                max_workers=self._max_workers,
            )
            self.jobs = MaintenanceJobCoordinator(
                max_workers=self._max_workers,
                operation_manager=self.operations,
            )
            self.services = RuntimeServices(
                stores=self.stores,
                gateway_factory=self.gateway_factory,
                jobs=self.jobs,
            )
            self.history = CampaignHistory(paths.data_dir)
            self.history.recover_interrupted()
            self._configure_pending_persistence()
            self._restore_pending_transactions()

    def close(self, *, wait_for_jobs: bool = True) -> None:
        self.operations.shutdown(wait=wait_for_jobs)
        self.jobs.shutdown(wait=wait_for_jobs)


DEFAULT_CAMPAIGN_RUNTIME = CampaignRuntime(
    configured_save_name(),
    paths=DEFAULT_CAMPAIGN_PATHS,
)

def get_campaign_runtime() -> CampaignRuntime:
    return DEFAULT_CAMPAIGN_RUNTIME
