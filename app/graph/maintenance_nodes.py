from __future__ import annotations

from app.domain.recall_memory.batch_classifier import MemoryBatchClassifier
from app.domain.recall_memory.maintainer import MemoryMaintainer
from app.domain.recall_memory.maintenance import (
    batch_classify_pending_memories_workflow,
    maintain_memories_workflow,
)
from app.domain.scene_summary.maintenance import _CONDENSE_LOCK
from app.domain.scene_summary.summarizer import SceneSummarizer
from app.runtime.serialization import model_data
from app.domain.director_npc.workflows import restock_director_npc_standby

from .maintenance_helpers import maintenance_gateway
from .maintenance_state import (
    DirectorNPCRestockState,
    MemoryMaintenanceState,
    SceneSummaryMaintenanceState,
)


def _services(state):
    return state["runtime_services"]


def _store_factory(state, name: str):
    return getattr(_services(state).stores, name)


def scene_summarize_node(
    state: SceneSummaryMaintenanceState,
) -> SceneSummaryMaintenanceState:
    summarizer = SceneSummarizer(
        maintenance_gateway(
            turn_index=state["turn_index"], services=_services(state)
        )
    )
    summary = summarizer.summarize_turn(
        scene_text=state["scene_text"],
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        scene_id=state["scene_id"],
        turn_index=state["turn_index"],
        trace_id=state["trace_id"],
    )
    with _store_factory(state, "scene_summary")() as store:
        saved_id = store.save_one(summary)

    result = state["result"]
    result.saved_l1_id = saved_id
    result.created_summaries.append(summary)
    return {
        "result": result,
        "events": [
            {
                "step": "scene_summarized",
                "data": {"saved_l1_id": saved_id},
            }
        ],
    }


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

    # Preserve the original lock scope across recursive levels and their LLM calls.
    with _CONDENSE_LOCK, _store_factory(state, "scene_summary")() as store:
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

    return {
        "result": result,
        "events": [
            {
                "step": "scene_summaries_condensed",
                "data": {"condensed_ids": result.condensed_ids},
            }
        ],
    }


def memory_batch_classify_node(
    state: MemoryMaintenanceState,
) -> MemoryMaintenanceState:
    classifier = MemoryBatchClassifier(
        maintenance_gateway(
            turn_index=state["turn_index"], services=_services(state)
        )
    )
    with (
        _store_factory(state, "pending_memory")() as pending_store,
        _store_factory(state, "memory")() as mem_store,
        _store_factory(state, "entity")() as ent_store,
        _store_factory(state, "scene_summary")() as summary_store,
    ):
        result = batch_classify_pending_memories_workflow(
            current_state=state["current_state"],
            core_memory=state["core_memory"],
            classifier=classifier,
            pending_store=pending_store,
            memory_store=mem_store,
            entity_store=ent_store,
            summary_store=summary_store,
            turn_threshold=state.get("turn_threshold", 5),
            force=state.get("force", False),
            trace_id=state["trace_id"],
        )
    return {
        "batch_result": result,
        "events": [
            {
                "step": "memory_batch_classified",
                "data": {
                    "triggered": result.triggered,
                    "pending_count": result.pending_count,
                    "saved_ids": result.saved_ids,
                    "classified_pending_ids": result.classified_pending_ids,
                },
            }
        ],
    }


def memory_maintain_node(
    state: MemoryMaintenanceState,
) -> MemoryMaintenanceState:
    maintainer = MemoryMaintainer(
        maintenance_gateway(
            turn_index=state.get("turn_index") or None,
            services=_services(state),
        )
    )
    with (
        _store_factory(state, "memory")() as mem_store,
        _store_factory(state, "scene_summary")() as summary_store,
    ):
        result = maintain_memories_workflow(
            store=mem_store,
            summary_store=summary_store,
            maintainer=maintainer,
            core_memory=state["core_memory"],
            through_turn=state.get("through_turn"),
            trace_id=state["trace_id"],
        )
    return {
        "maintenance_result": result,
        "events": [
            {
                "step": "memory_maintained",
                "data": {
                    "summaries": result.summary_count,
                    "changed": result.changed_count,
                    "cursor_before": result.cursor_before,
                    "cursor_after": result.cursor_after,
                    "proposals": model_data(result.proposals),
                },
            }
        ],
    }


def director_npc_restock_node(
    state: DirectorNPCRestockState,
) -> DirectorNPCRestockState:
    gateway = maintenance_gateway(
        turn_index=state["turn_index"], services=_services(state)
    )
    with (
        _store_factory(state, "director_npc")() as npc_store,
        _store_factory(state, "npc_profile")() as profile_store,
    ):
        created_drafts = restock_director_npc_standby(
            core_memory=state["core_memory"],
            protagonist_name=state["protagonist_name"],
            turn_index=state["turn_index"],
            director_npc_store=npc_store,
            npc_profile_store=profile_store,
            creator_gateway=gateway,
            trace_id=state["trace_id"],
        )
    return {
        "created_drafts": created_drafts,
        "events": [
            {
                "step": "director_npc_restocked",
                "data": {
                    "created_count": len(created_drafts),
                    "created_names": [draft.name for draft in created_drafts],
                },
            }
        ],
    }
