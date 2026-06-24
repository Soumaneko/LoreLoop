from dataclasses import dataclass, field
from threading import Lock

from app.domain.current_state.schemas import CurrentState
from app.domain.core_memory.schemas import CoreMemory
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_summary.store import SceneSummaryStore
from app.domain.scene_summary.summarizer import SceneSummarizer


_CONDENSE_LOCK = Lock()


@dataclass
class SceneSummaryMaintenanceResult:
    saved_l1_id: str | None = None
    condensed_ids: list[str] = field(default_factory=list)
    created_summaries: list[SceneSummary] = field(default_factory=list)


def run_scene_summary_maintenance(
    *,
    scene_text: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_id: str,
    turn_index: int,
    summarizer: SceneSummarizer,
    store: SceneSummaryStore,
    threshold: int = 10,
    trace_id: str | None = None,
) -> SceneSummaryMaintenanceResult:
    result = SceneSummaryMaintenanceResult()
    l1 = summarizer.summarize_turn(
        scene_text=scene_text,
        current_state=current_state,
        core_memory=core_memory,
        scene_id=scene_id,
        turn_index=turn_index,
        trace_id=trace_id,
    )
    result.saved_l1_id = store.save_one(l1)
    result.created_summaries.append(l1)

    with _CONDENSE_LOCK:
        level = 1
        while store.should_condense(level, threshold=threshold):
            sources = store.list_uncondensed(level, limit=threshold)
            if len(sources) < threshold:
                break
            condensed = summarizer.condense(
                source_summaries=sources,
                target_level=level + 1,
                core_memory=core_memory,
                trace_id=trace_id,
            )
            condensed_id = store.save_one(condensed)
            changed = store.mark_condensed(
                [summary.id for summary in sources],
                condensed_into=condensed_id,
            )
            if changed < len(sources):
                break
            result.condensed_ids.append(condensed_id)
            result.created_summaries.append(condensed)
            level += 1

    return result
