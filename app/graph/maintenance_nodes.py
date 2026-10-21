from __future__ import annotations

from app.core.diagnostics import diagnostics_from_state
from app.domain.scene_summary.maintenance import _CONDENSE_LOCK
from app.domain.scene_summary.summarizer import SceneSummarizer, SceneSummaryWarning
from app.runtime.serialization import model_data

from .maintenance_helpers import maintenance_gateway
from .maintenance_state import SceneSummaryMaintenanceState


def _services(state):
    return state["runtime_services"]


def scene_summary_condense_node(
    state: SceneSummaryMaintenanceState,
) -> SceneSummaryMaintenanceState:
    summarizer = SceneSummarizer(
        maintenance_gateway(
            turn_index=state["turn_index"], services=_services(state)
        )
    )
    result = state["result"]
    threshold = state.get("threshold", 10)
    warnings: list[SceneSummaryWarning] = []

    # Preserve the original lock scope across recursive levels and their LLM calls.
    with _CONDENSE_LOCK, _services(state).stores.scene_summary as store:
        level = 1
        while store.should_condense(level, threshold=threshold):
            sources = store.list_uncondensed(level, limit=threshold)
            if len(sources) < threshold:
                break
            condensed = summarizer.condense(
                source_summaries=sources,
                target_level=level + 1,
                core_memory=state["core_memory"],
                trace_id=state["trace_id"],
            )
            warnings.extend(summarizer.last_warnings)
            if condensed is None:
                break
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

    diagnostics = diagnostics_from_state(state)
    for warning in warnings:
        diagnostics.warning(
            source="scene_summary",
            code=warning.code,
            message=warning.message,
            trace_id=state["trace_id"],
            task="scene_summary_condense",
            turn_index=state["turn_index"],
        )

    return {
        "result": result,
        "events": [
            {
                "step": "scene_summaries_condensed",
                "data": {"condensed_ids": result.condensed_ids, "warning_codes": [warning.code for warning in warnings]},
            }
        ],
    }
