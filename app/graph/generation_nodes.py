from __future__ import annotations

from concurrent.futures import CancelledError

from langgraph.config import get_stream_writer

from app.core.diagnostics import NullDiagnosticLogger
from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.context_facts import ContextFactsInput, ContextFactsService
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.narrative_composer import (
    NarrativeComposerAgent,
    build_entity_retrieval_anchors,
)
from app.domain.narrative_rewrite import (
    NarrativeRecomposer,
    RecomposeRequest,
    SourceNarrativeRewriter,
    SourceRewriteRequest,
)
from app.domain.narrative_canon_query import (
    NarrativeCanonQueryAgent,
    build_entity_catalog,
)
from app.domain.canon_evidence import CanonEvidenceRetriever
from app.domain.narrative_consistency import (
    NarrativeConsistencyAgent,
    render_revision_instruction,
)
from app.domain.roll.resolver import RollResolver
from app.domain.roll.schemas import RollConfig
from app.domain.roll_modifier.evaluator import RollModifierEvaluator
from app.domain.roll_modifier.schemas import RollModifierBatch
from app.domain.turn_resolution import TurnResolutionAgent, TurnResolutionResult
from app.domain.turn_resolution.views import project_turn_resolution
from app.runtime.serialization import model_data
from app.domain.director.generation import DirectorGenerationExecutor
from app.domain.director.log import assemble_director_log_entry
from app.domain.npc_profile.selection import (
    merge_profile_selections,
    select_profiles_by_active_events,
    select_profiles_by_context_facts,
)
from app.domain.npc_profile.workflows import gather_npc_profiles_from_state_and_anchors

from .generation_helpers import assemble_narrative_context, gateway_for
from .generation_state import GenerationState


def _store_factory(state: GenerationState, name: str):
    return getattr(state["runtime_services"].stores, name)


def _diagnostics(state: GenerationState):
    services = state.get("runtime_services")
    return (
        getattr(services, "diagnostics", None)
        if services is not None
        else None
    ) or NullDiagnosticLogger()


def _composition_output_state(
    state: GenerationState,
    composition,
) -> GenerationState:
    outline = composition.to_outline()
    scene_result = composition.to_scene_result()
    beat_entity_anchors = build_entity_retrieval_anchors(outline)
    action_parse_result = state.get("action_parse_result")
    anchors = [
        *(action_parse_result.anchors if action_parse_result is not None else []),
        *beat_entity_anchors,
    ]
    # Context profiles were selected before composition. Re-select from the final
    # outline so newly planned entities are represented and NPCs use public views.
    with _store_factory(state, "npc_profile")() as npc_store:
        composer_npc_profiles = gather_npc_profiles_from_state_and_anchors(
            current_state=state["current_state"],
            anchors=anchors,
            npc_store=npc_store,
        )
    return {
        "narrative_composition": composition,
        "outline": outline,
        "beat_entity_anchors": beat_entity_anchors,
        "composer_npc_public_views": [
            profile.public_view()
            for profile in composer_npc_profiles
        ],
        "scene_result": scene_result,
    }


def extract_context_facts_node(state: GenerationState) -> GenerationState:
    action_parse_result = state.get("action_parse_result")
    try:
        with (
            _store_factory(state, "confirmed_history")() as history_store,
            _store_factory(state, "continuity")() as continuity_store,
            _store_factory(state, "world_setting")() as setting_store,
            _store_factory(state, "npc_profile")() as npc_profile_store,
        ):
            result = ContextFactsService(
                gateway_for(state),
                history_store,
                continuity_store,
                setting_store,
                npc_profile_store,
            ).build(
                ContextFactsInput(
                    user_input=state["user_input"],
                    core_memory=state["core_memory"],
                    current_state=state["current_state"],
                    scene_summaries=state["scene_summaries"],
                    matched_anchors=state.get("matched_anchors", []),
                    retrieval_topics=(
                        action_parse_result.retrieval_topics
                        if action_parse_result is not None
                        else []
                    ),
                    npc_profiles=[
                        profile.disclosure_view()
                        for profile in state.get("action_npc_profiles", [])
                    ],
                    current_turn_index=state["next_turn_index"],
                ),
                trace_id=state["trace_id"],
            )
    except CancelledError:
        raise
    except Exception as exc:
        error = str(exc)
        _diagnostics(state).warning(
            source="context_extractor",
            code="context_extract_failed",
            message=error,
            trace_id=state["trace_id"],
            task="context_extract",
            turn_index=state.get("next_turn_index") or state.get("turn_index"),
        )
        return {
            "context_facts": ContextFactSet(
                turn_focus="上下文事实提取失败",
                facts=[],
            ),
            "context_search_plan": None,
            "context_history_evidence": None,
            "continuity_selection": None,
            "context_extract_error": error,
            "context_fact_npc_profiles": [],
            "events": [{
                "step": "context_extract_failed",
                "data": {
                    "error": error,
                    "trace_id": state["trace_id"],
                },
            }],
        }
    try:
        with _store_factory(state, "npc_profile")() as npc_store:
            context_fact_npc_profiles = select_profiles_by_context_facts(
                context_facts=result.context_facts,
                npc_store=npc_store,
            )
    except Exception as exc:
        _diagnostics(state).warning(
            source="context_extractor",
            code="context_fact_npc_selection_failed",
            message=str(exc),
            trace_id=state["trace_id"],
            task="npc_selection",
            turn_index=state.get("next_turn_index") or state.get("turn_index"),
        )
        context_fact_npc_profiles = []
    return {
        "context_facts": result.context_facts,
        "context_search_plan": result.search_plan,
        "context_history_evidence": result.history_evidence,
        "continuity_selection": result.continuity,
        "context_extract_error": None,
        "context_fact_npc_profiles": context_fact_npc_profiles,
        "events": [{
            "step": "context_facts_done",
            "data": {
                "context_facts": model_data(result.context_facts),
                "context_search_plan": model_data(result.search_plan),
                "context_history_evidence": model_data(
                    result.history_evidence
                ),
                "continuity_selection": model_data(
                    result.continuity
                ),
                "world_settings": [
                    match.model_dump(mode="json")
                    for match in result.settings.settings
                ],
            },
        }],
    }


def parse_action_node(state: GenerationState) -> GenerationState:
    parser = ActionParser(gateway_for(state))
    with _store_factory(state, "director_event")() as event_store:
        hidden_events = event_store.list_hidden()
    action_result = parser.parse(
        user_input=state["user_input"],
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        events=hidden_events,
        trace_id=state["trace_id"],
    )
    raw_action_parse_result = ActionParseResult(
        analysis=action_result.analysis,
        atomic_actions=action_result.atomic_actions,
        anchors=state.get("matched_anchors", []),
        retrieval_topics=list(action_result.retrieval_topics),
        touched_event_ids=list(action_result.touched_event_ids),
    )
    self_check = ActionSelfCheck()
    action_parse_result = self_check.check(
        raw_action_parse_result,
        state["user_input"],
        state["current_state"],
    )

    return {
        "action_extraction_result": action_result,
        "raw_action_parse_result": raw_action_parse_result,
        "action_self_check_result": self_check.last_result,
        "action_parse_result": action_parse_result,
        "touched_event_candidates": list(action_parse_result.touched_event_ids),
        "events": [
            {
                "step": "action_parsed",
                "data": {
                    "raw_action_parse_result": model_data(raw_action_parse_result),
                    "action_self_check_result": model_data(self_check.last_result),
                    "action_parse_result": model_data(action_parse_result),
                    "touched_event_ids": list(action_parse_result.touched_event_ids),
                },
            },
        ],
    }


def evaluate_roll_modifiers_node(state: GenerationState) -> GenerationState:
    roll_config = RollConfig(seed=None, difficulty=state.get("difficulty", "困难"))
    roll_resolver = RollResolver(roll_config)
    roll_actions = roll_resolver.select_roll_actions(state["action_parse_result"])
    if not roll_actions:
        modifier_batch = RollModifierBatch()
        roll_results = roll_resolver.resolve(
            state["action_parse_result"],
            modifier_batch=modifier_batch,
            roll_actions=[],
        )
        return {
            "roll_config": roll_config,
            "roll_actions": [],
            "modifier_batch": modifier_batch,
            "roll_results": roll_results,
            "events": [{
                "step": "rolls_skipped",
                "data": {
                    "reason": "No actions require a roll.",
                    "roll_results": model_data(roll_results),
                },
            }],
        }

    modifier_batch = RollModifierEvaluator(gateway_for(state)).evaluate(
        parse_result=state["action_parse_result"],
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        roll_actions=roll_actions,
        trace_id=state["trace_id"],
    )
    roll_results = roll_resolver.resolve(
        state["action_parse_result"],
        modifier_batch=modifier_batch,
        roll_actions=roll_actions,
    )
    return {
        "roll_config": roll_config,
        "roll_actions": roll_actions,
        "modifier_batch": modifier_batch,
        "roll_results": roll_results,
        "events": [
            {
                "step": "modifiers_done",
                "data": {
                    "roll_actions": model_data(roll_actions),
                    "modifiers": model_data(modifier_batch),
                },
            },
            {
                "step": "rolls_done",
                "data": {
                    "roll_results": model_data(roll_results),
                },
            },
        ],
    }


def resolve_turn_node(state: GenerationState) -> GenerationState:
    if not state["action_parse_result"].atomic_actions:
        turn_resolution = TurnResolutionResult()
        return {
            "turn_resolution": turn_resolution,
            "events": [{
                "step": "turn_resolution_skipped",
                "data": {
                    "reason": "No atomic actions require resolution.",
                    "turn_resolution": model_data(turn_resolution),
                },
            }],
        }

    turn_resolution = TurnResolutionAgent(gateway_for(state)).resolve(
        user_input=state["user_input"],
        current_state=state["current_state"],
        action_parse_result=state["action_parse_result"],
        roll_results=state["roll_results"],
        core_memory=state["core_memory"],
        context_facts=state["context_facts"],
        npc_profiles=[
            profile.disclosure_view()
            for profile in state.get("action_npc_profiles", [])
        ],
        trace_id=state["trace_id"],
    )
    return {
        "turn_resolution": turn_resolution,
        "events": [{
            "step": "turn_resolution_done",
            "data": {
                "turn_resolution": model_data(turn_resolution),
            },
        }],
    }


def director_agent_stage_node(state: GenerationState) -> GenerationState:
    with (
        _store_factory(state, "npc_profile")() as npc_store,
        _store_factory(state, "director_event")() as event_store,
    ):
        event_npc_profiles = select_profiles_by_active_events(
            events=event_store.list_active(),
            npc_store=npc_store,
        )
        director_npc_profiles = merge_profile_selections(
            state.get("action_npc_profiles", []),
            state.get("context_fact_npc_profiles", []),
            event_npc_profiles,
        )
    stage, decision = DirectorGenerationExecutor(
        gateway=gateway_for(state),
        stores=state["runtime_services"].stores,
        core_memory=state["core_memory"],
        current_state=state["current_state"],
        user_input=state["user_input"],
        context_facts=[
            fact.fact for fact in state["context_facts"].facts
        ],
        recent_summaries=state.get("recent_summaries", []),
        turn_index=state["turn_index"],
        trace_id=state["trace_id"],
        turn_resolution_json=project_turn_resolution(state["turn_resolution"]),
        touched_event_candidates=state.get("touched_event_candidates", []),
        npc_profiles=director_npc_profiles,
    ).run()
    director_log_entry = assemble_director_log_entry(
        turn_index=state["turn_index"],
        phase="generation",
        decision=decision,
        stores=state["runtime_services"].stores,
    )
    return {
        "director_decision": decision,
        "director_stage": stage,
        "director_log_entry": director_log_entry,
        "events": [{
            "step": "director_agent_staged",
            "data": {
                "director_decision": model_data(decision),
                "director_stage": model_data(stage),
            },
        }],
    }


def _compose_with_context(
    state: GenerationState,
    context,
    *,
    context_event: str,
) -> GenerationState:
    stream_writer = get_stream_writer()
    recent_scene = (
        state["short_term_memory"].recent_scenes[-1]
        if state["short_term_memory"].recent_scenes
        else None
    )
    next_turn_index = state.get("next_turn_index") or (
        recent_scene.turn_index + 1 if recent_scene else 1
    )
    composition = NarrativeComposerAgent(gateway_for(state)).compose(
        context,
        recent_scene=recent_scene,
        turn_index=next_turn_index,
        trace_id=state["trace_id"],
        on_stream_start=lambda attempt: stream_writer({
            "step": "scene_stream_started",
            "data": {"attempt": attempt, "trace_id": state["trace_id"]},
        }),
        on_scene_text_delta=lambda delta: stream_writer({
            "step": "scene_text_delta",
            "data": {"delta": delta, "trace_id": state["trace_id"]},
        }),
    )
    output_state = _composition_output_state(state, composition)
    return {
        "context": context,
        **output_state,
        "recent_scene": recent_scene,
        "next_turn_index": next_turn_index,
        "events": [
            {
                "step": context_event,
                "data": {"context": model_data(context)},
            },
            {
                "step": "outline_done",
                "data": {
                    "plot_outline": model_data(output_state["outline"]),
                    "scene_reasoning": composition.scene_reasoning,
                },
            },
        ],
    }


def compose_narrative_node(state: GenerationState) -> GenerationState:
    composer_profiles = merge_profile_selections(
        state.get("action_npc_profiles", []),
        state.get("context_fact_npc_profiles") or [],
    )
    touched_event_ids = set(state.get("touched_event_candidates", []))
    with _store_factory(state, "director_event")() as event_store:
        engaged_events = [
            event
            for event in event_store.list_active()
            if event.stage == "engaged" or event.event_id in touched_event_ids
        ]
    director_stage = state.get("director_stage")
    intrude_ids = {
        material.resource_id
        for material in (
            director_stage.materials if director_stage is not None else []
        )
        if material.kind == "engaged_event"
    }
    engaged_events = [
        event
        for event in engaged_events
        if event.event_id in intrude_ids
    ]
    context = assemble_narrative_context(
        user_input=state["user_input"],
        current_state=state["current_state"],
        turn_resolution=state["turn_resolution"],
        core_memory=state["core_memory"],
        context_facts=state["context_facts"],
        npc_profiles=[
            profile.disclosure_view() for profile in composer_profiles
        ],
        director_stage=state.get("director_stage"),
        engaged_events=engaged_events,
    )
    return _compose_with_context(
        state,
        context,
        context_event="context_assembled",
    )


def rephrase_narrative_node(state: GenerationState) -> GenerationState:
    return _compose_with_context(
        state,
        state["context"],
        context_event="context_reused",
    )


def rewrite_from_source_node(state: GenerationState) -> GenerationState:
    decision = state["rewrite_strategy_decision"]
    composition = _run_source_rewrite(
        state,
        SourceRewriteRequest(
            source_scene_text=state["rewrite_source_scene_text"],
            requirements=decision.rewrite_requirements,
        ),
        event_kind="feedback_source",
    )
    output_state = _composition_output_state(state, composition)
    return {
        **output_state,
        "events": [{
            "step": "feedback_source_rewrite_done",
            "data": {
                "trace_id": state["trace_id"],
                "plot_outline": model_data(output_state["outline"]),
            },
        }],
    }


def recompose_from_feedback_node(state: GenerationState) -> GenerationState:
    decision = state["rewrite_strategy_decision"]
    runtime_kwargs = _rewrite_runtime_kwargs(
        state,
        rewrite_mode="recompose",
    )
    composition = NarrativeRecomposer(gateway_for(state)).recompose(
        state["context"],
        RecomposeRequest(requirements=decision.rewrite_requirements),
        **runtime_kwargs,
    )
    output_state = _composition_output_state(state, composition)
    return {
        **output_state,
        "events": [{
            "step": "feedback_recompose_done",
            "data": {
                "trace_id": state["trace_id"],
                "plot_outline": model_data(output_state["outline"]),
            },
        }],
    }


def generate_canon_queries_node(state: GenerationState) -> GenerationState:
    get_stream_writer()({
        "step": "canon_query_started",
        "data": {"trace_id": state["trace_id"]},
    })
    with _store_factory(state, "npc_profile")() as npc_store:
        npc_profiles = [
            profile.director_view()
            for profile in npc_store.list_all()
        ]
        entity_catalog = build_entity_catalog(
            player_name=state["current_state"].player.name,
            npc_profiles=npc_profiles,
        )
    query_set = NarrativeCanonQueryAgent(gateway_for(state)).generate(
        scene_text=state["narrative_composition"].scene_text,
        player_name=state["current_state"].player.name,
        npc_profiles=npc_profiles,
        trace_id=state["trace_id"],
    )
    return {
        "canon_entity_catalog": entity_catalog,
        "canon_query_set": query_set,
        "events": [{
            "step": "canon_queries_generated",
            "data": {
                "trace_id": state["trace_id"],
                "canon_query_set": model_data(query_set),
            },
        }],
    }


def retrieve_canon_evidence_node(state: GenerationState) -> GenerationState:
    with _store_factory(state, "confirmed_history")() as history_store:
        evidence_set = CanonEvidenceRetriever(
            history_store,
            state["canon_entity_catalog"],
        ).retrieve(
            state["canon_query_set"],
        )
    evidence_query_count = sum(
        1 for result in evidence_set.results if result.evidence
    )
    return {
        "canon_evidence_set": evidence_set,
        "events": [
            {
                "step": "canon_evidence_retrieved",
                "data": {
                    "trace_id": state["trace_id"],
                    "canon_evidence_set": model_data(evidence_set),
                    "queries_with_evidence": evidence_query_count,
                },
            },
            {
                "step": "narrative_check_started",
                "data": {"trace_id": state["trace_id"]},
            },
        ],
    }


def check_narrative_consistency_node(state: GenerationState) -> GenerationState:
    composition = state["narrative_composition"]
    agent = NarrativeConsistencyAgent(gateway_for(state))
    review = agent.check(
        user_input=state["user_input"],
        current_state=state["current_state"],
        turn_resolution_json=project_turn_resolution(state["turn_resolution"]),
        composition=composition,
        canon_query_set=state.get("canon_query_set"),
        canon_evidence_set=state.get("canon_evidence_set"),
        trace_id=state["trace_id"],
    )
    warnings = list(agent.last_warnings)
    for warning in warnings:
        warning_data = model_data(warning)
        _diagnostics(state).warning(
            source="narrative_consistency",
            code=warning.code,
            message=(
                "Ignored a canon contradiction assertion without a valid "
                "finding/evidence chain"
            ),
            trace_id=state["trace_id"],
            task="narrative_consistency_check",
            turn_index=state.get("next_turn_index") or state.get("turn_index"),
            details=warning_data,
        )
    return {
        "narrative_consistency_review": review,
        "narrative_consistency_warnings": warnings,
        "events": [{
            "step": "narrative_checked",
            "data": {
                "trace_id": state["trace_id"],
                "narrative_consistency_review": model_data(review),
                "warnings": model_data(warnings),
                "revision_required": review.decision == "revise",
            },
        }],
    }


def revise_narrative_node(state: GenerationState) -> GenerationState:
    review = state["narrative_consistency_review"]
    instruction = render_revision_instruction(review)
    composition = _run_source_rewrite(
        state,
        SourceRewriteRequest(
            source_scene_text=state["narrative_composition"].scene_text,
            requirements=[instruction],
        ),
        event_kind="consistency",
    )
    output_state = _composition_output_state(state, composition)
    return {
        **output_state,
        "narrative_revision_instruction": instruction,
        "events": [{
            "step": "narrative_revised",
            "data": {
                "trace_id": state["trace_id"],
                "revision_instruction": instruction,
                "plot_outline": model_data(output_state["outline"]),
                "scene_result": model_data(output_state["scene_result"]),
            },
        }],
    }


def _run_source_rewrite(
    state: GenerationState,
    request: SourceRewriteRequest,
    *,
    event_kind: str,
):
    runtime_kwargs = _rewrite_runtime_kwargs(
        state,
        rewrite_mode="revise_from_source",
        rewrite_origin=event_kind,
    )
    return SourceNarrativeRewriter(gateway_for(state)).rewrite(
        state["context"],
        request,
        **runtime_kwargs,
    )


def _rewrite_runtime_kwargs(
    state: GenerationState,
    *,
    rewrite_mode: str,
    rewrite_origin: str | None = None,
):
    stream_writer = get_stream_writer()
    if rewrite_origin is not None:
        stream_writer({
            "step": "narrative_revision_started",
            "data": {
                "trace_id": state["trace_id"],
                "rewrite_mode": rewrite_mode,
                "rewrite_origin": rewrite_origin,
            },
        })
    revision = rewrite_mode == "revise_from_source"
    return {
        "recent_scene": state.get("recent_scene"),
        "turn_index": state["next_turn_index"],
        "trace_id": state["trace_id"],
        "on_stream_start": lambda attempt: stream_writer({
            "step": "scene_stream_started",
            "data": {
                "attempt": attempt,
                "revision": revision,
                "rewrite_mode": rewrite_mode,
                "trace_id": state["trace_id"],
            },
        }),
        "on_scene_text_delta": lambda delta: stream_writer({
            "step": "scene_text_delta",
            "data": {
                "delta": delta,
                "revision": revision,
                "rewrite_mode": rewrite_mode,
                "trace_id": state["trace_id"],
            },
        }),
    }
