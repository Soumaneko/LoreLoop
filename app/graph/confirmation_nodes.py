from __future__ import annotations

import json

from app.core.diagnostics import NullDiagnosticLogger
from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.confirmed_history import build_turn_documents
from app.domain.continuity import ContinuityProjector
from app.domain.current_state import StateChangeSet, StateUpdater
from app.domain.director_event.reviewer import (
    DirectorEventEngagedReviewer,
    DirectorEventHiddenReviewer,
)
from app.domain.director_event.workflows import (
    review_engaged_events_workflow,
    review_hidden_events_workflow,
)
from app.domain.npc_profile.extractor import NPCProfileExtractor
from app.domain.npc_profile.workflows import (
    prune_npc_transient_aliases_from_current_state,
    update_npc_profiles_from_extraction,
)
from app.domain.scene_facts import SceneFactExtractInput, SceneFactExtractor
from app.domain.scene_summary.summarizer import SceneSummarizer
from app.domain.views import project_scene_facts
from app.domain.world_setting.schemas import setting_appears_in_text
from app.runtime.serialization import model_data

from .confirmation_cache import ConfirmationCache
from .confirmation_helpers import merge_unique_ids
from .confirmation_state import ConfirmationState
from app.domain.director.confirmation import DirectorConfirmationExecutor


def _gateway_for(state: ConfirmationState):
    pipeline_input = state["pipeline_input"]
    return state["runtime_services"].gateway(
        turn_index=pipeline_input.turn_index,
        session_type="confirmation",
    )


def _store_factory(state: ConfirmationState, name: str):
    return getattr(state["runtime_services"].stores, name)


def continuity_project_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    cache = ConfirmationCache(state["cache"])
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
        active_count = len(active_items)

    def compute_projection():
        return ContinuityProjector(_gateway_for(state)).project(
            current_documents=documents,
            active_items=active_items,
            trace_id=state["trace_id"],
        )

    projection = cache.compute_if_missing(
        "continuity_projection",
        compute_projection,
    )
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
    cache = ConfirmationCache(state["cache"])
    core_memory = state["core_memory"]

    def compute_scene_facts():
        return SceneFactExtractor(_gateway_for(state)).extract(
            SceneFactExtractInput(
                scene_text=state["effective_scene_text"],
                current_state=state["state_before_update"],
                core_memory=core_memory,
            ),
            trace_id=trace_id,
        )

    scene_facts = cache.compute_if_missing("scene_facts", compute_scene_facts)
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
    cache = ConfirmationCache(state["cache"])
    computed = False

    def compute_update():
        nonlocal computed
        computed = True
        gateway = _gateway_for(state)
        updater = StateUpdater(gateway)
        npc_aliases = {
            profile.name: [
                *profile.aliases,
                *list(profile.transient_aliases.keys()),
            ]
            for profile in state["existing_npc_profiles"]
            if profile.name
            and (profile.aliases or profile.transient_aliases)
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
        return updated_state, state_change_set, state_update_warnings

    updated_state, state_change_set, state_update_warnings = cache.atomic(
        "updated_state",
        "state_change_set",
        "state_update_warnings",
        factory=compute_update,
    )
    services = state.get("runtime_services")
    diagnostics = (
        getattr(services, "diagnostics", None)
        if services is not None
        else None
    ) or NullDiagnosticLogger()
    if computed:
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
    cache = ConfirmationCache(state["cache"])

    def compute_turn_summary():
        return SceneSummarizer(_gateway_for(state)).summarize_turn(
            scene_text=state["confirmation_scene_text"],
            current_state=state["updated_state"],
            core_memory=state["core_memory"],
            scene_id=state["updated_state"].scene.scene_id,
            turn_index=pipeline_input.turn_index,
            trace_id=state["trace_id"],
        )

    turn_summary = cache.compute_if_missing("turn_summary", compute_turn_summary)
    return {
        "turn_summary": turn_summary,
        "events": [{
            "step": "scene_summarized",
            "data": {"turn_summary": model_data(turn_summary)},
        }],
    }


def npc_profile_extract_node(state: ConfirmationState) -> ConfirmationState:
    if not state.get("write_db", True):
        return {"npc_extraction": None}
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = ConfirmationCache(state["cache"])

    def compute_npc_extraction():
        return NPCProfileExtractor(_gateway_for(state)).extract(
            scene_facts=state["scene_facts_json"],
            trace_id=trace_id,
        )

    npc_extraction = cache.compute_if_missing("npc_extraction", compute_npc_extraction)
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
    cache = ConfirmationCache(state["cache"])

    def compute_action_preview():
        scene_was_edited = (
            state["effective_scene_text"]
            != pipeline_input.scene_result.scene_text
        )
        return ActionPreviewAgent(_gateway_for(state)).generate(
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

    action_preview = cache.compute_if_missing("action_preview", compute_action_preview)
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
    if not state.get("write_db", True):
        cache = ConfirmationCache(state["cache"])
        return {
            "director_decision": cache.result("director_decision"),
            "director_executions": cache.result("director_executions"),
        }

    cache = ConfirmationCache(state["cache"])

    def compute_decision():
        return DirectorConfirmationExecutor(
            gateway=_gateway_for(state),
            stores=state["runtime_services"].stores,
            core_memory=state["core_memory"],
            current_state=state["updated_state"],
            user_input=pipeline_input.user_input,
            scene_facts=state["scene_facts"].model_dump(mode="json"),
            recent_summaries=state.get("recent_summaries", []),
            selection_summaries=state.get("selection_summaries", []),
            turn_index=pipeline_input.turn_index,
            trace_id=state["trace_id"],
            new_npc_profiles=state.get("new_npc_profiles", []),
            scene_text=state["effective_scene_text"],
            director_log_entry=getattr(
                state.get("pending"), "director_log_entry", None
            ),
        ).run()

    decision, executions = cache.atomic(
        "director_decision",
        "director_executions",
        factory=compute_decision,
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


def npc_profile_update_node(state: ConfirmationState) -> ConfirmationState:
    if not state.get("write_db", True) or state.get("npc_extraction") is None:
        cache = ConfirmationCache(state["cache"])
        return {
            "updated_npc_profile_ids": cache.result("updated_npc_profile_ids") or [],
            "new_npc_profiles": cache.result("new_npc_profiles") or [],
        }
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = ConfirmationCache(state["cache"])
    computed = False

    def compute_npc_update():
        nonlocal computed
        computed = True
        with _store_factory(state, "npc_profile")() as npc_store:
            gateway = _gateway_for(state)
            evidence_documents = {
                document.document_id: document
                for document in build_turn_documents(
                    scene_id=state["updated_state"].scene.scene_id,
                    turn_index=pipeline_input.turn_index,
                    user_input=pipeline_input.user_input,
                    scene_text=state["effective_scene_text"],
                )
                if document.source_type == "scene_paragraph"
            }
            npc_result = update_npc_profiles_from_extraction(
                scene_facts=state["scene_facts_json"],
                current_state=state["updated_state"],
                core_memory=state["core_memory"],
                gateway=gateway,
                npc_store=npc_store,
                existing_profiles=state["existing_npc_profiles"],
                extraction=state["npc_extraction"],
                evidence_documents=evidence_documents,
                recent_summaries=state.get("recent_summaries", []),
                trace_id=trace_id,
                turn_index=pipeline_input.turn_index,
            )
            alias_pruned_ids = prune_npc_transient_aliases_from_current_state(
                npc_store=npc_store,
                turn_index=pipeline_input.turn_index,
            )
            updated_npc_profile_ids = merge_unique_ids([*npc_result.saved_ids, *alias_pruned_ids])
            return (
                updated_npc_profile_ids,
                npc_result.new_profiles,
                npc_result.warnings,
            )

    updated_npc_profile_ids, new_npc_profiles, npc_update_warnings = cache.atomic(
        "updated_npc_profile_ids",
        "new_npc_profiles",
        "npc_update_warnings",
        factory=compute_npc_update,
    )
    services = state.get("runtime_services")
    diagnostics = (
        getattr(services, "diagnostics", None)
        if services is not None
        else None
    ) or NullDiagnosticLogger()
    dropped_events = []
    if computed:
        for warning in npc_update_warnings:
            diagnostics.warning(
                source="npc_profile_update",
                code=warning.code,
                message=warning.message,
                trace_id=trace_id,
                task="npc_profile_update",
                turn_index=getattr(pipeline_input, "turn_index", None),
                details={
                    "npc_name": warning.npc_name,
                    "field": warning.field_name,
                    "evidence_ids": list(warning.evidence_ids),
                },
            )
        if npc_update_warnings:
            dropped_events = [{
                "step": "npc_long_term_fields_dropped",
                "data": {
                    "count": len(npc_update_warnings),
                    "warnings": [
                        {
                            "npc_name": warning.npc_name,
                            "field": warning.field_name,
                            "code": warning.code,
                            "evidence_ids": list(warning.evidence_ids),
                        }
                        for warning in npc_update_warnings
                    ],
                },
            }]
    return {
        "updated_npc_profile_ids": updated_npc_profile_ids,
        "new_npc_profiles": new_npc_profiles,
        "npc_update_warnings": npc_update_warnings,
        "events": dropped_events,
    }


def setting_appearance_update_node(state: ConfirmationState) -> ConfirmationState:
    """Count once how many world settings appear in the confirmed scene text.

    Mirrors npc_profile_update_node: writes directly to the store, keeps its
    result in the write-scoped confirmation cache so re-entry never double
    counts, and rolls back with the confirmation as a whole on failure.
    """
    if not state.get("write_db", True):
        return {"recorded_setting_ids": []}
    cache = ConfirmationCache(state["cache"])
    if cache.produced("recorded_setting_ids"):
        return {"recorded_setting_ids": cache.result("recorded_setting_ids")}
    folded_text = state["effective_scene_text"].casefold()
    recorded_ids: list[str] = []
    if folded_text:
        with _store_factory(state, "world_setting")() as setting_store:
            for setting in setting_store.list_all():
                if setting_appears_in_text(setting.name, folded_text):
                    if setting_store.record_mention(setting.setting_id):
                        recorded_ids.append(setting.setting_id)
    return {
        "recorded_setting_ids": recorded_ids,
        "events": [{
            "step": "setting_appearances_recorded",
            "data": {"count": len(recorded_ids)},
        }],
    }


def director_event_review_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    if not state.get("write_db", True):
        return {
            "director_event_reviews": ConfirmationCache(
                state["cache"]
            ).result("director_event_reviews")
            or [],
        }

    cache = ConfirmationCache(state["cache"])
    if cache.produced("director_event_reviews"):
        return {"director_event_reviews": cache.result("director_event_reviews")}

    gateway = _gateway_for(state)
    turn_index = pipeline_input.turn_index
    reviews = []
    scene_facts_json = json.dumps(
        state["scene_facts"].model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    with _store_factory(state, "director_event")() as event_store:
        active_events = [
            event
            for event in event_store.list_active()
            if event.created_turn < turn_index
        ]
        hidden_events = [event for event in active_events if event.stage == "hidden"]
        engaged_events = [
            event for event in active_events if event.stage == "engaged"
        ]
        if hidden_events:
            hidden_results = review_hidden_events_workflow(
                active_events=hidden_events,
                user_input=pipeline_input.user_input,
                scene_facts=scene_facts_json,
                current_state=state["updated_state"],
                core_memory=state["core_memory"],
                scene_summaries=state.get("recent_summaries", []),
                turn_index=turn_index,
                store=event_store,
                reviewer=DirectorEventHiddenReviewer(gateway),
                trace_id=state["trace_id"],
            )
            reviews.extend(
                result.model_dump(mode="json")
                for result in hidden_results
            )
            engaged_ids = {event.event_id for event in engaged_events}
            for result in hidden_results:
                event = result.active_after
                if (
                    event is not None
                    and event.stage == "engaged"
                    and state["updated_state"].world_clock.has_reached(
                        event.deadline_clock
                    )
                    and event.event_id not in engaged_ids
                ):
                    engaged_events.append(event)
                    engaged_ids.add(event.event_id)
        if engaged_events:
            reviews.extend(
                result.model_dump(mode="json")
                for result in review_engaged_events_workflow(
                    active_events=engaged_events,
                    user_input=pipeline_input.user_input,
                    scene_facts=scene_facts_json,
                    current_state=state["updated_state"],
                    core_memory=state["core_memory"],
                    scene_summaries=state.get("recent_summaries", []),
                    turn_index=turn_index,
                    store=event_store,
                    reviewer=DirectorEventEngagedReviewer(gateway),
                    trace_id=state["trace_id"],
                )
            )

    return {
        "director_event_reviews": reviews,
        "events": [{
            "step": "director_event_reviewed",
            "data": {
                "director_event_reviews": reviews,
            },
        }],
    }
