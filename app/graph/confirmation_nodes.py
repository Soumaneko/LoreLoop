from __future__ import annotations

from app.core.llm.config import create_gateway
from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.current_state import StateUpdater
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.entity.extractor import EntityExtractor
from app.domain.npc_profile.extractor import NPCProfileExtractor
from app.domain.npc_profile.workflows import (
    sync_npc_profile_absence_from_current_state,
    update_npc_profiles_from_extraction,
)
from app.domain.recall_memory.draft_extractor import MemoryDraftExtractor
from app.domain.scene_facts import SceneFactExtractInput, SceneFactExtractor
from app.domain.views import project_scene_facts
from app.runtime.serialization import model_data
from app.runtime.stores import (
    director_event_store,
    director_npc_store,
    entity_store,
    npc_profile_store,
)
from app.domain.director_event.workflows import (
    DirectorEventWorkflowBatchResult,
    update_director_event_workflow,
)
from app.domain.entity.workflows import update_entity_profiles_from_extraction

from .confirmation_helpers import merge_unique_ids
from .confirmation_state import ConfirmationState


def scene_fact_extract_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    core_memory = state["core_memory"]
    scene_facts = cache.get("scene_facts")
    if scene_facts is None:
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        scene_facts = SceneFactExtractor(gateway).extract(
            SceneFactExtractInput(
                scene_text=state["effective_scene_text"],
                current_state=state["state_before_update"],
                core_memory=core_memory,
            ),
            trace_id=trace_id,
        )
    return {
        "scene_facts": scene_facts,
        "scene_facts_json": project_scene_facts(scene_facts),
        "events": [{
            "step": "scene_facts_done",
            "data": {
                "scene_facts": model_data(scene_facts),
            },
        }],
    }


def state_update_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    updated_state = cache.get("updated_state")
    if updated_state is None:
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        updater = StateUpdater(gateway)
        npc_aliases = {
            profile.name: list(profile.aliases)
            for profile in state["existing_npc_profiles"]
            if profile.name and profile.aliases
        }
        updated_state = updater.update(
            state["state_before_update"],
            state["scene_facts_json"],
            core_memory=state["core_memory"],
            npc_aliases=npc_aliases,
            trace_id=trace_id,
        )
    return {
        "updated_state": updated_state,
        "events": [{
            "step": "state_update_done",
            "data": {"state_after": model_data(updated_state)},
        }],
    }


def memory_draft_extract_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    pending_memory_result = cache.get("pending_memory_result")
    if pending_memory_result is None:
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        extractor = MemoryDraftExtractor(gateway)
        pending_memory_result = extractor.extract(
            state["scene_facts_json"],
            state["state_before_update"],
            core_memory=state["core_memory"],
            turn_index=pipeline_input.turn_index,
            scene_id=state["state_before_update"].scene.scene_id,
            trace_id=trace_id,
        )
    return {
        "pending_memory_result": pending_memory_result,
        "events": [{
            "step": "memory_draft_extract_done",
            "data": {
                "pending_items_count": len(pending_memory_result.items),
                "pending_items": model_data(pending_memory_result.items),
            },
        }],
    }


def entity_extract_node(state: ConfirmationState) -> ConfirmationState:
    if not state.get("write_db", True):
        return {"entity_extraction": None}
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    entity_extraction = cache.get("entity_extraction")
    if entity_extraction is None:
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        extractor = EntityExtractor(gateway)
        entity_extraction = extractor.extract(
            core_memory=state["core_memory"],
            scene_facts=state["scene_facts_json"],
            existing_profiles=state["existing_entity_profiles"],
            trace_id=trace_id,
        )
    return {
        "entity_extraction": entity_extraction,
        "events": [{
            "step": "entity_extract_done",
            "data": {
                "entity_extraction": model_data(entity_extraction),
            },
        }],
    }


def npc_profile_extract_node(state: ConfirmationState) -> ConfirmationState:
    if not state.get("write_db", True):
        return {"npc_extraction": None}
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    npc_extraction = cache.get("npc_extraction")
    with director_event_store() as event_store:
        active_events = event_store.list_active()
    if npc_extraction is None:
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        extractor = NPCProfileExtractor(gateway)
        npc_extraction = extractor.extract(
            core_memory=state["core_memory"],
            scene_facts=state["scene_facts_json"],
            existing_profiles=state["existing_npc_profiles"],
            director_events=active_events,
            trace_id=trace_id,
        )
    return {
        "npc_extraction": npc_extraction,
        "npc_director_events": active_events,
        "events": [{
            "step": "npc_extract_done",
            "data": {
                "npc_extraction": model_data(npc_extraction),
            },
        }],
    }


def action_preview_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    action_preview = cache.get("action_preview")
    if action_preview is None:
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        action_preview = ActionPreviewAgent(gateway).generate(
            current_state=state["updated_state"],
            core_memory=state["core_memory"],
            recent_summaries=state.get("recent_summaries", []),
            next_action_directions=pipeline_input.scene_result.next_action_directions,
            trace_id=trace_id,
        )
    return {
        "action_preview": action_preview,
        "events": [{
            "step": "action_preview_done",
            "data": {
                "action_preview": model_data(action_preview),
            },
        }],
    }


def director_event_update_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    if (
        not state.get("write_db", True)
        or not pipeline_input.update_director_event
        or pipeline_input.turn_index < 1
    ):
        return {"director_event_result": state["cache"].get("director_event_result")}

    trace_id = state["trace_id"]
    cache = state["cache"]
    director_event_result = cache.get("director_event_result")
    if director_event_result is None and "director_event_result" not in cache:
        with (
            director_event_store() as director_store,
            director_npc_store() as director_npc_repo,
            entity_store() as director_entity_repo,
            npc_profile_store() as director_npc_profile_repo,
        ):
            standby_director_npcs = director_npc_repo.list_standby()
            director_entity_profiles = [
                profile.semantic_view()
                for profile in director_entity_repo.list_all()
            ]
            director_npc_profiles = [
                profile.director_view()
                for profile in director_npc_profile_repo.list_all()
            ]
            results = {}
            for role in ("arc", "ambient"):
                reviewer_gateway = create_gateway()
                reviewer_gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
                selector_gateway = create_gateway()
                selector_gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
                results[role] = update_director_event_workflow(
                    role=role,
                    user_input=pipeline_input.user_input,
                    scene_facts=state["scene_facts_json"],
                    current_state=state["updated_state"],
                    core_memory=state["core_memory"],
                    scene_summaries=state.get("scene_summaries", []),
                    recent_summaries=state.get("recent_summaries", []),
                    entity_profiles=director_entity_profiles,
                    npc_profiles=director_npc_profiles,
                    turn_index=pipeline_input.turn_index,
                    store=director_store,
                    reviewer=DirectorEventReviewer(reviewer_gateway),
                    selector=DirectorEventSelector(selector_gateway),
                    standby_director_npcs=standby_director_npcs,
                    trace_id=trace_id,
                )
            director_event_result = DirectorEventWorkflowBatchResult(**results)
    return {"director_event_result": director_event_result}


def entity_profile_update_node(state: ConfirmationState) -> ConfirmationState:
    if not state.get("write_db", True) or state.get("entity_extraction") is None:
        return {"updated_entity_ids": state["cache"].get("updated_entity_ids", [])}
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    updated_entity_ids = cache.get("updated_entity_ids", [])
    if "updated_entity_ids" not in cache:
        with entity_store() as ent_store:
            gateway = create_gateway()
            gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
            entity_result = update_entity_profiles_from_extraction(
                scene_facts=state["scene_facts_json"],
                current_state=state["updated_state"],
                core_memory=state["core_memory"],
                gateway=gateway,
                entity_store=ent_store,
                existing_profiles=state["existing_entity_profiles"],
                extraction=state["entity_extraction"],
                recent_summaries=state.get("recent_summaries", []),
                trace_id=trace_id,
            )
            updated_entity_ids = entity_result.saved_ids
    return {"updated_entity_ids": updated_entity_ids}


def npc_profile_update_node(state: ConfirmationState) -> ConfirmationState:
    if not state.get("write_db", True) or state.get("npc_extraction") is None:
        return {"updated_npc_profile_ids": state["cache"].get("updated_npc_profile_ids", [])}
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    updated_npc_profile_ids = cache.get("updated_npc_profile_ids", [])
    if "updated_npc_profile_ids" not in cache:
        with npc_profile_store() as npc_store:
            gateway = create_gateway()
            gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
            npc_result = update_npc_profiles_from_extraction(
                scene_facts=state["scene_facts_json"],
                current_state=state["updated_state"],
                core_memory=state["core_memory"],
                gateway=gateway,
                npc_store=npc_store,
                existing_profiles=state["existing_npc_profiles"],
                extraction=state["npc_extraction"],
                director_events=state.get("npc_director_events", []),
                recent_summaries=state.get("recent_summaries", []),
                trace_id=trace_id,
            )
            absence_synced_ids = sync_npc_profile_absence_from_current_state(
                npc_store=npc_store,
                current_state=state["updated_state"],
            )
            updated_npc_profile_ids = merge_unique_ids([*npc_result.saved_ids, *absence_synced_ids])
    return {"updated_npc_profile_ids": updated_npc_profile_ids}
