from __future__ import annotations

from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.current_state import StateChangeSet, StateUpdater
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.intervention_planner import (
    DirectorEventInterventionPlanner,
)
from app.domain.director_event.intervention_reviewer import (
    DirectorEventInterventionReviewer,
)
from app.domain.director_event.selector import DirectorEventSelector
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
from app.domain.director_event.workflows import (
    DirectorEventWorkflowBatchResult,
    update_director_event_workflow,
)
from app.domain.entity.workflows import update_entity_profiles_from_extraction

from .confirmation_helpers import ensure_director_npc_profile, merge_unique_ids
from .confirmation_state import ConfirmationState


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
    values = [
        value
        for match in extraction.alias_matches
        for value in (
            match.extracted_text,
            match.matched_npc_name,
            match.new_alias,
        )
        if value
    ]
    values.extend(
        value
        for npc in extraction.retrieved_npcs
        for value in (npc.name, npc.matched_npc_name)
        if value
    )
    return list(dict.fromkeys(values))


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
    elif state_change_set is None:
        state_change_set = StateChangeSet(
            decision="State update failed; no operations were applied."
        )
    return {
        "updated_state": updated_state,
        "state_change_set": state_change_set,
        "events": [{
            "step": "state_update_done",
            "data": {
                "state_after": model_data(updated_state),
                "state_changes": model_data(state_change_set),
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
    with _store_factory(state, "director_event")() as event_store:
        active_events = event_store.list_active()
    if npc_extraction is None:
        gateway = _gateway_for(state)
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
            _store_factory(state, "director_event")() as director_store,
            _store_factory(state, "director_npc")() as director_npc_repo,
            _store_factory(state, "entity")() as director_entity_repo,
            _store_factory(state, "npc_profile")() as director_npc_profile_repo,
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
            entity_mentions = _entity_extraction_mentions(
                state.get("entity_extraction")
            )
            npc_mentions = _npc_extraction_mentions(state.get("npc_extraction"))
            results = {}
            for role in ("arc", "ambient"):
                reviewer_gateway = _gateway_for(state)
                intervention_review_gateway = _gateway_for(state)
                intervention_plan_gateway = _gateway_for(state)
                selector_gateway = _gateway_for(state)
                results[role] = update_director_event_workflow(
                    role=role,
                    user_input=pipeline_input.user_input,
                    scene_facts=state["scene_facts_json"],
                    current_state=state["updated_state"],
                    core_memory=state["core_memory"],
                    scene_summaries=state.get("scene_summaries", []),
                    recent_summaries=state.get("recent_summaries", []),
                    selection_summaries=state.get("selection_summaries", []),
                    entity_profiles=director_entity_profiles,
                    npc_profiles=director_npc_profiles,
                    entity_mentions=entity_mentions,
                    npc_mentions=npc_mentions,
                    turn_index=pipeline_input.turn_index,
                    store=director_store,
                    reviewer=DirectorEventReviewer(reviewer_gateway),
                    intervention_reviewer=DirectorEventInterventionReviewer(
                        intervention_review_gateway
                    ),
                    intervention_planner=DirectorEventInterventionPlanner(
                        intervention_plan_gateway
                    ),
                    selector=DirectorEventSelector(selector_gateway),
                    standby_director_npcs=standby_director_npcs,
                    trace_id=trace_id,
                )
                # Scene Facts above are confined to reviewing/intervening on an
                # existing event. DirectorEventSelector's contract receives the
                # recent summaries, never this turn's Scene Facts.
                active_after = results[role].active_after
                if active_after is not None and active_after.director_npc_id:
                    promoted = ensure_director_npc_profile(
                        active_after.director_npc_id,
                        turn_index=pipeline_input.turn_index,
                        stores=state["runtime_services"].stores,
                    )
                    if promoted is None:
                        raise ValueError(
                            "DirectorEvent references an unavailable DirectorNPC"
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
