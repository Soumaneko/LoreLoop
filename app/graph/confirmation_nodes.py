from __future__ import annotations

from app.core.diagnostics import NullDiagnosticLogger
from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.confirmed_history import build_turn_documents
from app.domain.continuity import ContinuityProjector
from app.domain.current_state import StateChangeSet, StateUpdater
from app.domain.entity.extractor import EntityExtractor
from app.domain.entity.schemas import EntityExtractionResult
from app.domain.npc_profile.extractor import NPCProfileExtractor
from app.domain.npc_profile.schemas import NPCExtractionResult
from app.domain.npc_profile.workflows import (
    sync_npc_profile_absence_from_current_state,
    update_npc_profiles_from_extraction,
)
from app.domain.scene_facts import SceneFactExtractInput, SceneFactExtractor
from app.domain.scene_summary.summarizer import SceneSummarizer
from app.domain.views import project_scene_facts
from app.runtime.serialization import model_data
from app.domain.entity.workflows import update_entity_profiles_from_extraction

from .confirmation_helpers import merge_unique_ids
from .confirmation_state import ConfirmationState
from .director_agent import run_confirmation_director_agent


def _gateway_for(state: ConfirmationState):
    pipeline_input = state["pipeline_input"]
    return state["runtime_services"].gateway(
        turn_index=pipeline_input.turn_index,
        session_type="confirmation",
    )


def _store_factory(state: ConfirmationState, name: str):
    return getattr(state["runtime_services"].stores, name)


def _entity_extraction_mentions(
    extraction: EntityExtractionResult | None,
) -> list[str]:
    if extraction is None:
        return []
    values = [
        value
        for match in extraction.alias_matches
        for value in (
            match.extracted_text,
            match.matched_entity_name,
            match.new_alias,
        )
        if value
    ]
    values.extend(
        value
        for entity in extraction.retrieved_entities
        for value in (entity.name, entity.matched_entity_name)
        if value
    )
    return list(dict.fromkeys(values))


def _npc_extraction_mentions(extraction: NPCExtractionResult | None) -> list[str]:
    if extraction is None:
        return []
    return list(dict.fromkeys(mention.name for mention in extraction.mentions))


def continuity_project_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    documents = build_turn_documents(
        scene_id=state["updated_state"].scene.scene_id,
        turn_index=pipeline_input.turn_index,
        user_input=pipeline_input.user_input,
        scene_text=state["effective_scene_text"],
    )
    with _store_factory(state, "continuity")() as continuity_store:
        active_items = (
            []
            if pipeline_input.clear_continuity_before_save
            else continuity_store.list_active()
        )
        projection = ContinuityProjector(_gateway_for(state)).project(
            current_documents=documents,
            active_items=active_items,
            trace_id=state["trace_id"],
        )
        active_count = len(active_items)
    return {
        "continuity_projection": projection,
        "events": [{
            "step": "continuity_projected",
            "data": {
                "active_count": active_count,
                "continuity_projection": model_data(projection),
            },
        }],
    }


def scene_fact_extract_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    core_memory = state["core_memory"]
    scene_facts = cache.get("scene_facts")
    if scene_facts is None:
        gateway = _gateway_for(state)
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
    state_change_set = cache.get("state_change_set")
    state_update_warnings = cache.get("state_update_warnings")
    update_executed = updated_state is None
    if updated_state is None:
        gateway = _gateway_for(state)
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
        state_change_set = getattr(
            updater,
            "last_change_set",
            StateChangeSet(decision="State update produced no change set."),
        )
        state_update_warnings = list(getattr(updater, "last_warnings", []))
    elif state_change_set is None:
        state_change_set = StateChangeSet(
            decision="State update failed; no operations were applied."
        )
    if state_update_warnings is None:
        state_update_warnings = []
    services = state.get("runtime_services")
    diagnostics = (
        getattr(services, "diagnostics", None)
        if services is not None
        else None
    ) or NullDiagnosticLogger()
    if update_executed:
        for warning in state_update_warnings:
            warning_data = model_data(warning)
            diagnostics.warning(
                source="state_update",
                code=warning.code,
                message=warning.message,
                trace_id=trace_id,
                task="state_update",
                turn_index=getattr(pipeline_input, "turn_index", None),
                details=warning_data,
            )
    return {
        "updated_state": updated_state,
        "state_change_set": state_change_set,
        "state_update_warnings": state_update_warnings,
        "events": [{
            "step": "state_update_done",
            "data": {
                "state_after": model_data(updated_state),
                "state_changes": model_data(state_change_set),
                "warnings": model_data(state_update_warnings),
            },
        }],
    }


def scene_summarize_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    cache = state["cache"]
    turn_summary = cache.get("turn_summary")
    if turn_summary is None:
        turn_summary = SceneSummarizer(_gateway_for(state)).summarize_turn(
            scene_text=state["confirmation_scene_text"],
            current_state=state["updated_state"],
            core_memory=state["core_memory"],
            scene_id=state["updated_state"].scene.scene_id,
            turn_index=pipeline_input.turn_index,
            trace_id=state["trace_id"],
        )
    return {
        "turn_summary": turn_summary,
        "events": [{
            "step": "scene_summarized",
            "data": {"turn_summary": model_data(turn_summary)},
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
        gateway = _gateway_for(state)
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
    if npc_extraction is None:
        gateway = _gateway_for(state)
        extractor = NPCProfileExtractor(gateway)
        npc_extraction = extractor.extract(
            scene_facts=state["scene_facts_json"],
            trace_id=trace_id,
        )
    return {
        "npc_extraction": npc_extraction,
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
        scene_was_edited = (
            state["effective_scene_text"]
            != pipeline_input.scene_result.scene_text
        )
        gateway = _gateway_for(state)
        action_preview = ActionPreviewAgent(gateway).generate(
            current_state=state["updated_state"],
            core_memory=state["core_memory"],
            recent_summaries=state.get("recent_summaries", []),
            next_action_directions=(
                []
                if scene_was_edited
                else pipeline_input.scene_result.next_action_directions
            ),
            scene_facts=state["scene_facts"],
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


def director_agent_confirm_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    if (
        not state.get("write_db", True)
        or not pipeline_input.run_director_agent
    ):
        return {
            "director_decision": state["cache"].get("director_decision"),
            "director_executions": state["cache"].get("director_executions"),
        }

    cache = state["cache"]
    decision = cache.get("director_decision")
    executions = cache.get("director_executions")
    if decision is None or executions is None:
        with (
            _store_factory(state, "entity")() as director_entity_repo,
            _store_factory(state, "npc_profile")() as director_npc_profile_repo,
        ):
            director_entity_profiles = [
                profile.semantic_view()
                for profile in director_entity_repo.list_all()
            ]
            director_npc_profiles = [
                profile.director_view()
                for profile in director_npc_profile_repo.list_all()
            ]
            entity_mentions = _entity_extraction_mentions(
                state.get("entity_extraction")
            )
            npc_mentions = _npc_extraction_mentions(state.get("npc_extraction"))
            decision, executions = run_confirmation_director_agent(
                gateway=_gateway_for(state),
                stores=state["runtime_services"].stores,
                core_memory=state["core_memory"],
                current_state=state["updated_state"],
                user_input=pipeline_input.user_input,
                scene_facts=state["scene_facts"].model_dump(mode="json"),
                recent_summaries=state.get("recent_summaries", []),
                selection_summaries=state.get("selection_summaries", []),
                entity_profiles=director_entity_profiles,
                npc_profiles=director_npc_profiles,
                entity_mentions=entity_mentions,
                npc_mentions=npc_mentions,
                turn_index=pipeline_input.turn_index,
                trace_id=state["trace_id"],
            )
    return {
        "director_decision": decision,
        "director_executions": executions,
        "events": [{
            "step": "director_agent_confirmed",
            "data": {
                "director_decision": model_data(decision),
                "director_executions": model_data(executions),
            },
        }],
    }


def entity_profile_update_node(state: ConfirmationState) -> ConfirmationState:
    if not state.get("write_db", True) or state.get("entity_extraction") is None:
        return {"updated_entity_ids": state["cache"].get("updated_entity_ids", [])}
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = state["cache"]
    updated_entity_ids = cache.get("updated_entity_ids", [])
    if "updated_entity_ids" not in cache:
        with _store_factory(state, "entity")() as ent_store:
            gateway = _gateway_for(state)
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
        with _store_factory(state, "npc_profile")() as npc_store:
            gateway = _gateway_for(state)
            npc_result = update_npc_profiles_from_extraction(
                scene_facts=state["scene_facts_json"],
                current_state=state["updated_state"],
                core_memory=state["core_memory"],
                gateway=gateway,
                npc_store=npc_store,
                existing_profiles=state["existing_npc_profiles"],
                extraction=state["npc_extraction"],
                recent_summaries=state.get("recent_summaries", []),
                trace_id=trace_id,
            )
            absence_synced_ids = sync_npc_profile_absence_from_current_state(
                npc_store=npc_store,
                current_state=state["updated_state"],
            )
            updated_npc_profile_ids = merge_unique_ids([*npc_result.saved_ids, *absence_synced_ids])
    return {"updated_npc_profile_ids": updated_npc_profile_ids}
