import concurrent.futures
from dataclasses import dataclass

from app.domain.core_memory import InitConfig
from app.domain.current_state import CurrentState
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory import ShortTermMemory
from app.workflows.context_assembler import PlotOutlineContext


BACKGROUND_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2)


@dataclass
class PendingTurn:
    turn_id: str
    user_input: str
    state_before: CurrentState
    context: PlotOutlineContext
    outline: PlotOutlineResult
    scene_result: SceneWriterResult
    selected_memory_ids: list[str]
    turn_index: int = 1
    short_term_memory: ShortTermMemory | None = None


@dataclass
class PendingOpening:
    turn_id: str
    config: InitConfig
    current_state: CurrentState
    scene_result: SceneWriterResult


PENDING_TURNS: dict[str, PendingTurn] = {}
PENDING_OPENINGS: dict[str, PendingOpening] = {}
