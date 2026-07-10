import concurrent.futures
import threading
from dataclasses import dataclass, field
from typing import Any

from app.domain.core_memory import InitConfig
from app.domain.current_state import CurrentState
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.director_npc.schemas import DirectorNPCWorkflowResult
from app.domain.short_term_memory import ShortTermMemory
from app.domain.plot_outline.schemas import PlotOutlineContext


BACKGROUND_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)
_BACKGROUND_JOBS_GUARD = threading.RLock()
_MEMORY_BATCH_FUTURE: concurrent.futures.Future | None = None
_MEMORY_BATCH_RERUN: tuple | None = None


@dataclass
class PendingTurn:
    turn_id: str
    user_input: str
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


PENDING_TURNS: dict[str, PendingTurn] = {}
PENDING_OPENINGS: dict[str, PendingOpening] = {}

_CONFIRM_SERIAL_LOCK = threading.Lock()


def confirmation_lock(turn_id: str) -> threading.Lock:
    return _CONFIRM_SERIAL_LOCK


def release_confirmation_lock(turn_id: str) -> None:
    return None


def submit_memory_batch_once(fn, /, *args, **kwargs) -> bool:
    global _MEMORY_BATCH_FUTURE, _MEMORY_BATCH_RERUN
    with _BACKGROUND_JOBS_GUARD:
        if _MEMORY_BATCH_FUTURE is not None and not _MEMORY_BATCH_FUTURE.done():
            _MEMORY_BATCH_RERUN = (fn, args, kwargs)
            return True
        _MEMORY_BATCH_FUTURE = _submit_memory_batch(fn, args, kwargs)
        return True


def _submit_memory_batch(fn, args, kwargs):
    future = BACKGROUND_EXECUTOR.submit(fn, *args, **kwargs)
    future.add_done_callback(_memory_batch_finished)
    return future


def _memory_batch_finished(_future) -> None:
    global _MEMORY_BATCH_FUTURE, _MEMORY_BATCH_RERUN
    with _BACKGROUND_JOBS_GUARD:
        rerun = _MEMORY_BATCH_RERUN
        _MEMORY_BATCH_RERUN = None
        if rerun is None:
            _MEMORY_BATCH_FUTURE = None
            return
        fn, args, kwargs = rerun
        _MEMORY_BATCH_FUTURE = _submit_memory_batch(fn, args, kwargs)
