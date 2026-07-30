from __future__ import annotations

from app.domain.scene_summary.maintenance import _CONDENSE_LOCK
from app.domain.scene_summary.summarizer import SceneSummarizer
from app.runtime.serialization import model_data
from app.domain.director_npc.workflows import restock_director_npc_standby
from app.domain.world_setting.workflows import extract_setting_seeds

from .maintenance_helpers import maintenance_gateway
from .maintenance_state import (
    DirectorNPCMaintenanceState,
    SceneSummaryMaintenanceState,
)


def _services(state):
    return state["runtime_services"]


def _store_factory(state, name: str):
    return getattr(_services(state).stores, name)


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


def setting_seed_extract_node(
    state: DirectorNPCMaintenanceState,
) -> DirectorNPCMaintenanceState:
    gateway = maintenance_gateway(
        turn_index=state["turn_index"], services=_services(state)
    )
    with (
        _store_factory(state, "world_setting")() as setting_store,
        _store_factory(state, "npc_profile")() as profile_store,
    ):
        result = extract_setting_seeds(
            core_memory=state["core_memory"],
            scene_text=state["scene_text"],
            scene_facts_json=state["scene_facts_json"],
            turn_index=state["turn_index"],
            new_npc_profile_ids=state.get("new_npc_profile_ids", []),
            world_setting_store=setting_store,
            npc_profile_store=profile_store,
            gateway=gateway,
            trace_id=state["trace_id"],
        )
    return {
        "setting_seed_result": result,
        "events": [
            {
                "step": "setting_seeds_extracted",
                "data": {
                    "saved_setting_ids": result.saved_setting_ids,
                    "candidate_count": len(result.extraction.candidates),
                    "ignored_candidate_count": result.ignored_candidate_count,
                },
            }
        ],
    }


def director_npc_restock_node(
    state: DirectorNPCMaintenanceState,
) -> DirectorNPCMaintenanceState:
    gateway = maintenance_gateway(
        turn_index=state["turn_index"], services=_services(state)
    )
    with (
        _store_factory(state, "director_npc")() as npc_store,
        _store_factory(state, "npc_profile")() as profile_store,
        _store_factory(state, "world_setting")() as setting_store,
    ):
        created_drafts = restock_director_npc_standby(
            core_memory=state["core_memory"],
            protagonist_name=state["protagonist_name"],
            turn_index=state["turn_index"],
            director_npc_store=npc_store,
            world_setting_store=setting_store,
            npc_profile_store=profile_store,
            creator_gateway=gateway,
            setting_maintainer_gateway=gateway,
            setting_preparer_gateway=gateway,
            reservation_owner=f"{state['trace_id']}:{state['turn_index']}",
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
