import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from app.domain.core_memory import InitConfig
from app.domain.current_state import CurrentState
from app.domain.roll.schemas import Difficulty
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.director.schemas import DirectorStageResult
from app.domain.short_term_memory import ShortTermMemory
from app.domain.turn_resolution.schemas import PlayerTurnResolution
from app.runtime.paths import (
    CampaignPaths,
    DEFAULT_CAMPAIGN_PATHS,
    configured_save_id,
)
from app.runtime.services import RuntimeServices
from app.runtime.stores import StoreBundle
from app.runtime.jobs import MaintenanceJobCoordinator
from app.runtime.operations import OperationManager
from app.runtime.model_config import effective_gateway_factory
from app.core.diagnostics import NullDiagnosticLogger
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
    director_stage: DirectorStageResult | None = None
    player_resolution: PlayerTurnResolution = field(
        default_factory=PlayerTurnResolution
    )
    turn_index: int = 1
    short_term_memory: ShortTermMemory | None = None
    confirmation_cache: dict[str, Any] = field(default_factory=dict)
    original_scene_text: str | None = None
    token_usage: dict[str, Any] | None = None


@dataclass
class PendingOpening:
    turn_id: str
    group_id: str
    version_index: int
    initialization_fingerprint: str
    config: InitConfig
    current_state: CurrentState
    scene_result: SceneWriterResult
    confirmation_cache: dict[str, Any] = field(default_factory=dict)
    original_scene_text: str | None = None
    token_usage: dict[str, Any] | None = None


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
        try:
            if self._on_add:
                self._on_add(pending)
        except BaseException:
            with self._lock:
                if self._items.get(pending.turn_id) is pending:
                    self._items.pop(pending.turn_id, None)
            raise

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
        try:
            if self._on_add:
                self._on_add(pending)
        except BaseException:
            with self._lock:
                if self._items.get(pending.turn_id) is pending:
                    self._items.pop(pending.turn_id, None)
            raise

    def variants(self, group_id: str) -> list[PendingOpening]:
        with self._lock:
            return sorted(
                (
                    pending
                    for pending in self._items.values()
                    if pending.group_id == group_id
                ),
                key=lambda pending: pending.version_index,
            )

    def remove(self, turn_id: str) -> None:
        with self._lock:
            self._items.pop(turn_id, None)
        if self._on_remove:
            self._on_remove([turn_id])

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
        self.gateway_factory = gateway_factory or effective_gateway_factory()
        self.diagnostics = getattr(
            self.gateway_factory,
            "diagnostic_logger",
            NullDiagnosticLogger(),
        )
        self._max_workers = max_workers
        self.pending_turns = PendingTurnRepository()
        self.pending_openings = PendingOpeningRepository()
        self.operations = OperationManager(
            campaign_id,
            max_workers=max_workers,
            diagnostics=self.diagnostics,
        )
        self.jobs = MaintenanceJobCoordinator(
            max_workers=max_workers,
            operation_manager=self.operations,
        )
        self.services = RuntimeServices(
            stores=self.stores,
            gateway_factory=self.gateway_factory,
            jobs=self.jobs,
            diagnostics=self.diagnostics,
        )
        self._confirmation_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._save_task_active = False
        self._on_save_played = None
        from app.runtime.initialization import InitializationStore

        self.initialization = InitializationStore(self.paths.initialization_json)
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

    def is_model_configured(self) -> bool:
        configured = getattr(self.gateway_factory, "is_configured", None)
        return True if configured is None else bool(configured)

    def replace_gateway_factory(self, factory, *, persist=None) -> None:
        """Atomically install model settings for future pipeline work."""
        with self._lifecycle_lock:
            if self._save_task_active:
                raise RuntimeError("A save management task is currently running")
            if self.has_active_operations():
                raise RuntimeError(
                    "模型任务正在运行，请等待当前生成、确认或维护完成后再修改配置"
                )
            with self._confirmation_lock:
                self.jobs.wait_for_idle()
                if persist is not None:
                    persist()
                self.gateway_factory = factory
                self.diagnostics = getattr(
                    factory,
                    "diagnostic_logger",
                    self.diagnostics,
                )
                self.operations.diagnostics = self.diagnostics
                self.services = RuntimeServices(
                    stores=self.stores,
                    gateway_factory=factory,
                    jobs=self.jobs,
                    diagnostics=self.diagnostics,
                )

    def configure_save_touch(self, callback) -> None:
        self._on_save_played = callback

    def _touch_save_played(self) -> None:
        if self._on_save_played is not None:
            try:
                self._on_save_played()
            except (OSError, ValueError):
                # Narrative state is already committed at this point. A
                # secondary display timestamp must never turn that success
                # into a retryable confirmation failure.
                pass

    @contextmanager
    def operation_submission(self):
        """Serialize pipeline submission against save lifecycle mutations."""
        with self._lifecycle_lock:
            if self._save_task_active:
                raise RuntimeError("A save management task is currently running")
            yield

    @contextmanager
    def save_task(self):
        """Reserve this runtime while a save is copied, moved, or archived."""
        with self._lifecycle_lock:
            if self._save_task_active:
                raise RuntimeError("Another save management task is already running")
            if self.has_active_operations():
                raise RuntimeError(
                    "Current save has an active operation. Wait for it to finish."
                )
            self._save_task_active = True
        try:
            with self._confirmation_lock:
                yield
        finally:
            with self._lifecycle_lock:
                self._save_task_active = False

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

    def save_pending_scene_edit(
        self,
        turn_id: str,
        scene_text: str | None,
    ) -> dict[str, Any]:
        with self._confirmation_lock:
            pending = self.pending_turns.get(turn_id)
            opening = self.pending_openings.get(turn_id)
            selected = pending or opening
            if selected is None:
                raise ValueError("Pending turn not found")

            source_text = (
                selected.original_scene_text
                if selected.original_scene_text is not None
                else selected.scene_result.scene_text
            )
            requested_text = source_text if scene_text is None else str(scene_text)
            if not requested_text.strip():
                raise ValueError("Scene text cannot be empty")

            selected.scene_result = selected.scene_result.model_copy(
                update={"scene_text": requested_text}
            )
            selected.original_scene_text = (
                None if requested_text == source_text else source_text
            )
            persisted = (
                self._persist_pending_turn(selected)
                if isinstance(selected, PendingTurn)
                else self._persist_pending_opening(selected)
            )
            if not persisted:
                raise RuntimeError("Pending scene edit could not be persisted")
            return {
                "turn_id": selected.turn_id,
                "scene_text": source_text,
                "saved_scene_text": (
                    selected.scene_result.scene_text
                    if selected.original_scene_text is not None
                    else None
                ),
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
            "director_stage": model_data(pending.director_stage),
            "player_resolution": model_data(pending.player_resolution),
            "turn_index": pending.turn_index,
            "short_term_memory": model_data(pending.short_term_memory),
            "original_scene_text": pending.original_scene_text,
            "token_usage": pending.token_usage,
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
            "group_id": pending.group_id,
            "version_index": pending.version_index,
            "initialization_fingerprint": pending.initialization_fingerprint,
            "config": model_data(pending.config),
            "current_state": model_data(pending.current_state),
            "scene_result": model_data(pending.scene_result),
            "original_scene_text": pending.original_scene_text,
            "token_usage": pending.token_usage,
            "confirmation_cache": {},
        }
        self.history.save_pending(
            turn_id=pending.turn_id,
            kind="opening",
            group_id=pending.group_id,
            turn_index=0,
            payload=payload,
        )
        return True

    def _restore_pending_transactions(self) -> None:
        for record in self.history.pending_records():
            payload = record["payload_json"]
            import json

            data = json.loads(payload)
            required_fields = (
                {
                    "turn_id",
                    "group_id",
                    "version_index",
                    "user_input",
                    "difficulty",
                    "max_chars",
                    "state_before",
                    "context",
                    "outline",
                    "scene_result",
                    "turn_index",
                    "original_scene_text",
                }
                if record["kind"] == "turn"
                else {
                    "turn_id",
                    "group_id",
                    "version_index",
                    "initialization_fingerprint",
                    "config",
                    "current_state",
                    "scene_result",
                    "original_scene_text",
                }
            )
            missing_fields = sorted(required_fields.difference(data))
            if missing_fields:
                raise RuntimeError(
                    f"Pending {record['kind']} journal {record['turn_id']} "
                    "does not match the current storage contract; missing fields: "
                    f"{', '.join(missing_fields)}"
                )
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
                    director_stage=(
                        DirectorStageResult.model_validate(data["director_stage"])
                        if data.get("director_stage") else None
                    ),
                    player_resolution=PlayerTurnResolution.model_validate(
                        data.get("player_resolution") or {}
                    ),
                    turn_index=data["turn_index"],
                    short_term_memory=(
                        ShortTermMemory.model_validate(data["short_term_memory"])
                        if data.get("short_term_memory") else None
                    ),
                    original_scene_text=data["original_scene_text"],
                    token_usage=data.get("token_usage"),
                ))
            else:
                self.pending_openings.restore(PendingOpening(
                    turn_id=data["turn_id"],
                    group_id=data["group_id"],
                    version_index=data["version_index"],
                    initialization_fingerprint=data["initialization_fingerprint"],
                    config=InitConfig.model_validate(data["config"]),
                    current_state=CurrentState.model_validate(data["current_state"]),
                    scene_result=SceneWriterResult.model_validate(data["scene_result"]),
                    original_scene_text=data["original_scene_text"],
                    token_usage=data.get("token_usage"),
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
        self.history.complete_confirmation(pending.turn_id, pending.group_id)
        self._touch_save_played()

    def undo(self) -> dict[str, Any]:
        with self._confirmation_lock:
            if self.has_pending_content():
                raise RuntimeError("Confirm or discard pending content before undo")
            self.jobs.wait_for_idle()
            result = self.history.undo()
            self._restore_pending_transactions()
            self._restart_jobs()
            self._touch_save_played()
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
            self._touch_save_played()
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
            diagnostics=self.diagnostics,
        )

    def reconfigure(
        self,
        campaign_id: str,
        paths: CampaignPaths,
    ) -> None:
        """Switch this runtime to another persisted campaign directory."""
        with self._confirmation_lock:
            if self.has_active_operations():
                raise RuntimeError(
                    "Current save has an active operation. Wait for it to finish "
                    "before switching saves."
                )
            self.operations.shutdown(wait=True)
            self.jobs.shutdown(wait=True)
            # Pending content belongs to the source campaign's durable journal.
            # Switching only unloads its in-memory projection; it must not
            # mutate the source journal.
            self.pending_turns.clear(discard=False)
            self.pending_openings.clear(discard=False)
            self.campaign_id = campaign_id
            self.paths = paths
            self.stores = StoreBundle(paths)
            from app.runtime.initialization import InitializationStore

            self.initialization = InitializationStore(paths.initialization_json)
            self.operations = OperationManager(
                campaign_id,
                max_workers=self._max_workers,
                diagnostics=self.diagnostics,
            )
            self.jobs = MaintenanceJobCoordinator(
                max_workers=self._max_workers,
                operation_manager=self.operations,
            )
            self.services = RuntimeServices(
                stores=self.stores,
                gateway_factory=self.gateway_factory,
                jobs=self.jobs,
                diagnostics=self.diagnostics,
            )
            self.history = CampaignHistory(paths.data_dir)
            self.history.recover_interrupted()
            self._configure_pending_persistence()
            self._restore_pending_transactions()

    def close(self, *, wait_for_jobs: bool = True) -> None:
        self.operations.shutdown(wait=wait_for_jobs)
        self.jobs.shutdown(wait=wait_for_jobs)

    def begin_shutdown(self) -> int:
        return self.operations.cancel_active(stop_accepting_new=True)


DEFAULT_CAMPAIGN_RUNTIME = CampaignRuntime(
    configured_save_id(),
    paths=DEFAULT_CAMPAIGN_PATHS,
)

def get_campaign_runtime() -> CampaignRuntime:
    return DEFAULT_CAMPAIGN_RUNTIME
