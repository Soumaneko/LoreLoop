from __future__ import annotations

from concurrent.futures import CancelledError

from langgraph.config import get_stream_writer

from app.core.diagnostics import NullDiagnosticLogger
from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.context_facts.extractor import ContextExtractor
from app.domain.context_facts.schemas import ContextExtractInput
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.director_event.consumer import DirectorEventConsumer
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
from app.domain.director_npc.workflows import maybe_director_npc_workflow
from app.domain.retrieval.service import retrieve_candidates
from app.domain.entity.workflows import gather_entity_profiles_from_state_and_anchors
from app.domain.npc_profile.workflows import gather_npc_profiles_from_state_and_anchors

from .generation_context_helpers import retrieve_context_fact_memories
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
    with (
        _store_factory(state, "entity")() as ent_store,
        _store_factory(state, "npc_profile")() as npc_store,
    ):
        composer_entity_profiles = gather_entity_profiles_from_state_and_anchors(
            current_state=state["current_state"],
            anchors=anchors,
            entity_store=ent_store,
        )
        composer_npc_profiles = gather_npc_profiles_from_state_and_anchors(
            current_state=state["current_state"],
            anchors=anchors,
            npc_store=npc_store,
        )
    return {
        "narrative_composition": composition,
        "outline": outline,
        "beat_entity_anchors": beat_entity_anchors,
        "composer_entity_profile_views": [
            profile.semantic_view()
            for profile in composer_entity_profiles
        ],
        "composer_npc_public_views": [
            profile.public_view()
            for profile in composer_npc_profiles
        ],
        "scene_result": scene_result,
    }


def extract_context_facts_node(state: GenerationState) -> GenerationState:
    try:
        with _store_factory(state, "memory")() as mem_store:
            context_fact_memories = retrieve_context_fact_memories(
                memory_store=mem_store,
                anchors=state.get("matched_anchors", []),
            )
            context_facts = ContextExtractor(gateway_for(state)).extract(
                ContextExtractInput(
                    user_input=state["user_input"],
                    core_memory=state["core_memory"],
                    current_state=state["current_state"],
                    scene_summaries=state["scene_summaries"],
                    retrieved_memories=context_fact_memories,
                    entity_profiles=state.get("action_entity_profile_views", []),
                    npc_profiles=state.get("action_npc_director_views", []),
                ),
                trace_id=state["trace_id"],
            )
        return {
            "context_facts": context_facts,
            "context_fact_memories": context_fact_memories,
            "context_extract_error": None,
            "events": [{
                "step": "context_facts_done",
                "data": {
                    "context_facts": model_data(context_facts),
                    "context_fact_memories": model_data(context_fact_memories),
                },
            }],
        }
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
                turn_focus="Context extract failed",
                facts=[],
            ),
            "context_fact_memories": None,
            "context_extract_error": error,
            "events": [{
                "step": "context_extract_failed",
                "data": {
                    "error": error,
                    "trace_id": state["trace_id"],
                },
            }],
        }


def parse_action_node(state: GenerationState) -> GenerationState:
    parser = ActionParser(gateway_for(state))
    action_result = parser.extract_actions(
        user_input=state["user_input"],
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        recent_summaries=state.get("recent_summaries", []),
        entity_profiles=state.get("action_entity_profile_views", []),
        npc_profiles=state.get("action_npc_director_views", []),
        trace_id=state["trace_id"],
    )
    raw_action_parse_result = ActionParseResult(
        analysis=action_result.analysis,
        atomic_actions=action_result.atomic_actions,
        anchors=state.get("matched_anchors", []),
        ambiguities=[],
    )
    self_check = ActionSelfCheck()
    action_parse_result = self_check.check(
        raw_action_parse_result,
        state["user_input"],
        state["current_state"],
    )

    with _store_factory(state, "memory")() as mem_store:
        retrieved_memories = retrieve_candidates(
            action_parse_result,
            mem_store,
        )

    return {
        "action_extraction_result": action_result,
        "raw_action_parse_result": raw_action_parse_result,
        "action_self_check_result": self_check.last_result,
        "action_parse_result": action_parse_result,
        "retrieved_memories": retrieved_memories,
        "events": [
            {
                "step": "action_parsed",
                "data": {
                    "raw_action_parse_result": model_data(raw_action_parse_result),
                    "action_self_check_result": model_data(self_check.last_result),
                    "action_parse_result": model_data(action_parse_result),
                },
            },
            {
                "step": "retrieval_done",
                "data": {
                    "retrieved_memories": model_data(retrieved_memories),
                },
            },
        ],
    }


def consume_director_events_node(state: GenerationState) -> GenerationState:
    consumptions = DirectorEventConsumer(gateway_for(state)).consume(
        events=state.get("active_director_events", []),
        user_input=state["user_input"],
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        context_facts=state["context_facts"],
        turn_resolution=state["turn_resolution"],
        entity_profiles=state.get("action_entity_profile_views", []),
        npc_profiles=state.get("action_npc_director_views", []),
        trace_id=state["trace_id"],
    )
    return {
        "director_event_consumptions": consumptions,
        "events": [{
            "step": "director_events_consumed",
            "data": {
                "director_event_consumptions": model_data(consumptions),
            },
        }],
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
        entity_profiles=state.get("action_entity_profile_views", []),
        npc_profiles=state.get("action_npc_director_views", []),
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


def maybe_director_npc_node(state: GenerationState) -> GenerationState:
    active_events = state.get("active_director_events", [])
    with (
        _store_factory(state, "director_npc")() as npc_store,
        _store_factory(state, "npc_profile")() as profile_store,
    ):
        director_npc_result = maybe_director_npc_workflow(
            core_memory=state["core_memory"],
            current_state=state["current_state"],
            recent_summaries=state.get("recent_summaries", []),
            director_events=active_events,
            user_input=state["user_input"],
            turn_index=state["turn_index"],
            director_npc_store=npc_store,
            npc_profile_store=profile_store,
            dispatcher_gateway=gateway_for(state),
            trace_id=state["trace_id"],
        )
    return {
        "director_npc_result": director_npc_result,
        "events": [{
            "step": "director_npc_done",
            "data": {
                "director_npc_result": model_data(director_npc_result),
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
    director_npc_result = state.get("director_npc_result")
    context = assemble_narrative_context(
        user_input=state["user_input"],
        current_state=state["current_state"],
        turn_resolution=state["turn_resolution"],
        core_memory=state["core_memory"],
        context_facts=state["context_facts"],
        entity_profiles=state.get("action_entity_profile_views", []),
        npc_profiles=state.get("action_npc_director_views", []),
        director_event_consumptions=state.get("director_event_consumptions", []),
        director_npc_injection=(
            director_npc_result.plot_injection
            if director_npc_result is not None
            else None
        ),
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
    with (
        _store_factory(state, "entity")() as entity_store,
        _store_factory(state, "npc_profile")() as npc_store,
    ):
        entity_profiles = [
            profile.semantic_view()
            for profile in entity_store.list_all()
        ]
        npc_profiles = [
            profile.director_view()
            for profile in npc_store.list_all()
        ]
        entity_catalog = build_entity_catalog(
            player_name=state["current_state"].player.name,
            entity_profiles=entity_profiles,
            npc_profiles=npc_profiles,
        )
    query_set = NarrativeCanonQueryAgent(gateway_for(state)).generate(
        scene_text=state["narrative_composition"].scene_text,
        player_name=state["current_state"].player.name,
        entity_profiles=entity_profiles,
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
    with (
        _store_factory(state, "short_term")() as short_term_store,
        _store_factory(state, "confirmed_history")() as history_store,
    ):
        history_store.sync(short_term_store.list_all())
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
