from __future__ import annotations

import json
from typing import Any

from app.core.diagnostics import diagnostics_from_state as _diagnostics
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
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
from app.domain.director.scheduled import collect_scheduled_npcs
from app.domain.npc_profile.workflows import (
    apply_confirmed_npc_turn,
)
from app.domain.scene_facts import (
    SceneFactExtractInput,
    SceneFactExtractor,
    SceneParagraphInput,
)
from app.domain.scene_summary.summarizer import SceneSummarizer
from app.domain.views import project_scene_facts
from app.domain.world_setting.schemas import setting_appears_in_text
from app.runtime.serialization import model_data

from .confirmation_cache import ConfirmationCache
from .confirmation_state import ConfirmationState
from app.domain.director.confirmation import DirectorConfirmationExecutor


def _gateway_for(state: ConfirmationState):
    pipeline_input = state["pipeline_input"]
    return state["runtime_services"].gateway(
        turn_index=pipeline_input.turn_index,
        session_type="confirmation",
    )


def continuity_project_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    cache = ConfirmationCache(state["cache"])
    documents = build_turn_documents(
        scene_id=state["updated_state"].scene.scene_id,
        turn_index=pipeline_input.turn_index,
        user_input=pipeline_input.user_input,
        scene_text=state["effective_scene_text"],
    )
    active_items = state["active_continuity_items"]
    active_count = len(active_items)
    computed = False
    projector: ContinuityProjector | None = None

    def compute_projection():
        nonlocal computed, projector
        computed = True
        projector = ContinuityProjector(_gateway_for(state))
        return projector.project(
            current_documents=documents,
            active_items=active_items,
            trace_id=state["trace_id"],
        )

    projection = cache.compute_if_missing(
        "continuity_projection",
        compute_projection,
    )
    diagnostics = _diagnostics(state)
    if computed and projector is not None:
        for warning in getattr(projector, "last_warnings", []):
            diagnostics.warning(
                source="continuity",
                code=warning.code,
                message=warning.message,
                trace_id=state["trace_id"],
                task="continuity_project",
                turn_index=getattr(pipeline_input, "turn_index", None),
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


def _extract_staged_npc_materials(
    pending,
    npc_store,
) -> list[dict[str, Any]]:
    """Identity clues for the NPCs the Director arranged into this turn.

    Uses the same ``collect_scheduled_npcs`` fold as Composer, so engaged-event
    actors, ``present_event`` actors, introduced drafts, and upgraded profiles
    all become one uniform clue list. That lets scene-fact extraction bind a
    prose-only description (e.g. "灰衣人") to the true NPC without leaking the
    name into prose.
    """
    stage = getattr(pending, "director_stage", None)
    if stage is None or not getattr(stage, "materials", None):
        return []
    return [
        {
            "name": entry.name,
            "role": entry.role or "",
            "appearance": entry.appearance or "",
            "personality_trait": entry.personality_trait or "",
            "guidance": entry.guidance or "",
            "source": entry.source,
        }
        for entry in collect_scheduled_npcs(stage.materials, npc_store)
    ]


def scene_fact_extract_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = ConfirmationCache(state["cache"])
    core_memory = state["core_memory"]
    pending = state.get("pending") or getattr(pipeline_input, "pending", None)
    with state["runtime_services"].stores.npc_profile as npc_store:
        staged_npcs = _extract_staged_npc_materials(pending, npc_store)
    scene_paragraphs = [
        SceneParagraphInput(
            document_id=document.document_id,
            text=document.text,
        )
        for document in build_turn_documents(
            scene_id=state["state_before_update"].scene.scene_id,
            turn_index=pipeline_input.turn_index,
            user_input=pipeline_input.user_input,
            scene_text=state["effective_scene_text"],
        )
        if document.source_type == "scene_paragraph"
    ]

    computed = False

    def compute_scene_facts():
        nonlocal computed
        computed = True
        return SceneFactExtractor(_gateway_for(state)).extract(
            SceneFactExtractInput(
                scene_text=state["effective_scene_text"],
                current_state=state["state_before_update"],
                core_memory=core_memory,
                scene_paragraphs=scene_paragraphs,
                staged_npcs=staged_npcs,
            ),
            trace_id=trace_id,
        )

    scene_facts = cache.compute_if_missing("scene_facts", compute_scene_facts)
    diagnostics = _diagnostics(state)
    if computed and scene_facts.degraded:
        reasons = set(scene_facts.degraded_reasons)
        if reasons & {"facts", "provider", "parse", "structure"} or not reasons:
            diagnostics.warning(
                source="scene_facts",
                code="scene_facts_degraded",
                message="Scene Facts 降级：facts 或整体输出不可用。",
                trace_id=trace_id,
                task="scene_facts",
                turn_index=getattr(pipeline_input, "turn_index", None),
            )
        if "npc_mentions" in reasons:
            diagnostics.warning(
                source="scene_facts",
                code="npc_mentions_degraded",
                message="Scene Facts 降级：npc_mentions 不可用或部分被清理。",
                trace_id=trace_id,
                task="scene_facts",
                turn_index=getattr(pipeline_input, "turn_index", None),
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
        paragraph_texts = {
            document.document_id: document.text
            for document in build_turn_documents(
                scene_id=state["state_before_update"].scene.scene_id,
                turn_index=pipeline_input.turn_index,
                user_input=pipeline_input.user_input,
                scene_text=state["effective_scene_text"],
            )
            if document.source_type == "scene_paragraph"
        }
        updated_state = updater.update(
            state["state_before_update"],
            state["scene_facts_json"],
            core_memory=state["core_memory"],
            npc_aliases=npc_aliases,
            paragraph_texts=paragraph_texts,
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
    diagnostics = _diagnostics(state)
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
    computed = False
    summarizer: SceneSummarizer | None = None

    def compute_turn_summary():
        nonlocal computed, summarizer
        computed = True
        summarizer = SceneSummarizer(_gateway_for(state))
        return summarizer.summarize_turn(
            scene_text=state["confirmation_scene_text"],
            current_state=state["updated_state"],
            core_memory=state["core_memory"],
            scene_id=state["updated_state"].scene.scene_id,
            turn_index=pipeline_input.turn_index,
            trace_id=state["trace_id"],
        )

    turn_summary = cache.compute_if_missing("turn_summary", compute_turn_summary)
    diagnostics = _diagnostics(state)
    if computed and summarizer is not None:
        for warning in getattr(summarizer, "last_warnings", []):
            diagnostics.warning(
                source="scene_summary",
                code=warning.code,
                message=warning.message,
                trace_id=state["trace_id"],
                task="scene_summarize",
                turn_index=getattr(pipeline_input, "turn_index", None),
            )
    return {
        "turn_summary": turn_summary,
        "events": [{
            "step": "scene_summarized",
            "data": {"turn_summary": model_data(turn_summary)},
        }],
    }


def action_preview_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = ConfirmationCache(state["cache"])
    computed = False
    agent: ActionPreviewAgent | None = None

    def compute_action_preview():
        nonlocal computed, agent
        scene_was_edited = (
            state["effective_scene_text"]
            != pipeline_input.scene_result.scene_text
        )
        computed = True
        agent = ActionPreviewAgent(_gateway_for(state))
        return agent.generate(
            current_state=state["updated_state"],
            core_memory=state["core_memory"],
            recent_summaries=state.get("recent_summaries", []),
            next_action_directions=(
                []
                if scene_was_edited
                else pipeline_input.scene_result.next_action_directions
            ),
            scene_facts=state["scene_facts"],
            scene_text=(
                state["effective_scene_text"]
                if not state["scene_facts"].facts
                else None
            ),
            trace_id=trace_id,
        )

    action_preview = cache.compute_if_missing("action_preview", compute_action_preview)
    diagnostics = _diagnostics(state)
    if computed and agent is not None:
        for warning in getattr(agent, "last_warnings", []):
            diagnostics.warning(
                source="action_preview",
                code=warning.code,
                message=warning.message,
                trace_id=trace_id,
                task="action_preview",
                turn_index=getattr(pipeline_input, "turn_index", None),
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
    if not state.get("write_db", True):
        cache = ConfirmationCache(state["cache"])
        return {
            "director_decision": cache.result("director_decision"),
            "director_executions": cache.result("director_executions"),
        }

    cache = ConfirmationCache(state["cache"])
    computed = False
    executor: DirectorConfirmationExecutor | None = None

    def compute_decision():
        nonlocal computed, executor
        computed = True
        executor = DirectorConfirmationExecutor(
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
            scene_text=state["effective_scene_text"],
            director_log_entry=getattr(
                state.get("pending"), "director_log_entry", None
            ),
        )
        return executor.run()

    decision, executions = cache.atomic(
        "director_decision",
        "director_executions",
        factory=compute_decision,
    )
    diagnostics = _diagnostics(state)
    if computed and executor is not None:
        for warning in getattr(executor, "last_warnings", []):
            diagnostics.warning(
                source="director",
                code=warning.code,
                message=warning.message,
                trace_id=state["trace_id"],
                task="director_agent_confirm",
                turn_index=getattr(pipeline_input, "turn_index", None),
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
    cache = ConfirmationCache(state["cache"])
    if not state.get("write_db", True):
        return {"updated_npc_profile_ids": []}
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    computed = False

    def compute_npc_update():
        nonlocal computed
        computed = True
        with state["runtime_services"].stores.npc_profile as npc_store:
            pending = state.get("pending") or getattr(
                pipeline_input,
                "pending",
                None,
            )
            return apply_confirmed_npc_turn(
                pending=pending,
                npc_store=npc_store,
                gateway=_gateway_for(state),
                core_memory=state["core_memory"],
                scene_facts=state["scene_facts"],
                current_state=state["updated_state"],
                user_input=pipeline_input.user_input,
                scene_text=state["effective_scene_text"],
                turn_index=pipeline_input.turn_index,
                scene_id=state["updated_state"].scene.scene_id,
                trace_id=trace_id,
            )

    node_result = cache.compute_if_missing("npc_turn_result", compute_npc_update)
    warnings = node_result.process_result.warnings
    warning_summary = [
        {
            "npc_name": warning.npc_name,
            "field": warning.field_name,
            "code": warning.code,
            "evidence_ids": list(warning.evidence_ids),
        }
        for warning in warnings
    ]
    diagnostics = _diagnostics(state)
    update_events = []
    if computed:
        for warning in warnings:
            diagnostics.warning(
                source="npc_batch_update",
                code=warning.code,
                message=warning.message,
                trace_id=trace_id,
                task="npc_batch_update",
                turn_index=getattr(pipeline_input, "turn_index", None),
                details={
                    "npc_name": warning.npc_name,
                    "field": warning.field_name,
                    "evidence_ids": list(warning.evidence_ids),
                },
            )
        update_events = [{
            "step": "npc_profile_updated",
            "data": {
                "updated_npc_profile_ids": node_result.updated_npc_profile_ids,
                "new_npc_candidates": [
                    {
                        "name": candidate.primary_surface,
                        "alias_surfaces": [
                            relation.surface
                            for relation in candidate.alias_relations
                        ],
                    }
                    for candidate in (
                        node_result.process_result.new_npc_candidates
                    )
                ],
                "warnings": warning_summary,
            },
        }]
    return {
        "updated_npc_profile_ids": node_result.updated_npc_profile_ids,
        "npc_warnings": warning_summary,
        "events": update_events,
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
        with state["runtime_services"].stores.world_setting as setting_store:
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
    review_warnings: list[tuple[str, str]] = []
    scene_facts_json = json.dumps(
        state["scene_facts"].model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    with state["runtime_services"].stores.director_event as event_store:
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
            try:
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
            except (
                LLMOutputParseError,
                LLMOutputValidationError,
            ) as exc:
                review_warnings.append((
                    "director_event_review_fallback",
                    f"Hidden event review failed; skipped: {exc}",
                ))
            else:
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
            try:
                engaged_results = review_engaged_events_workflow(
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
            except (
                LLMOutputParseError,
                LLMOutputValidationError,
            ) as exc:
                review_warnings.append((
                    "director_event_review_fallback",
                    f"Engaged event review failed; skipped: {exc}",
                ))
            else:
                reviews.extend(
                    result.model_dump(mode="json")
                    for result in engaged_results
                )

    diagnostics = _diagnostics(state)
    for code, message in review_warnings:
        diagnostics.warning(
            source="director_event",
            code=code,
            message=message,
            trace_id=state["trace_id"],
            task="director_event_review",
            turn_index=getattr(pipeline_input, "turn_index", None),
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
