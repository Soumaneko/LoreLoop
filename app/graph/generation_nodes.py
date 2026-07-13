from __future__ import annotations

from langgraph.config import get_stream_writer

from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.context_facts.extractor import ContextExtractor
from app.domain.context_facts.schemas import ContextExtractInput
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.director_event.consumer import DirectorEventConsumer
from app.domain.plot_outline.agent import PlotOutlineAgent
from app.domain.roll.resolver import RollResolver
from app.domain.roll.schemas import RollConfig
from app.domain.roll_modifier.evaluator import RollModifierEvaluator
from app.domain.roll_modifier.schemas import RollModifierBatch
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.helpers import build_entity_retrieval_anchors
from app.domain.turn_resolution import TurnResolutionAgent, TurnResolutionResult
from app.runtime.serialization import model_data
from app.runtime.stores import (
    director_npc_store,
    entity_store,
    memory_store,
    npc_profile_store,
)
from app.domain.director_npc.workflows import maybe_director_npc_workflow
from app.domain.retrieval.service import retrieve_candidates
from app.domain.entity.workflows import gather_entity_profiles_from_state_and_anchors
from app.domain.npc_profile.workflows import gather_npc_profiles_from_state_and_anchors

from .generation_context_helpers import retrieve_context_fact_memories
from .generation_helpers import assemble_context, gateway_for
from .generation_state import GenerationState


def extract_context_facts_node(state: GenerationState) -> GenerationState:
    try:
        with memory_store() as mem_store:
            context_fact_memories = retrieve_context_fact_memories(
                user_input=state["user_input"],
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
    except Exception as exc:
        error = str(exc)
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
        turn_intent=action_result.turn_intent,
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

    with memory_store() as mem_store:
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
    with director_npc_store() as npc_store, npc_profile_store() as profile_store:
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


def generate_plot_outline_node(state: GenerationState) -> GenerationState:
    director_npc_result = state.get("director_npc_result")
    context = assemble_context(
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
    outline = PlotOutlineAgent(gateway_for(state)).generate(
        context,
        trace_id=state["trace_id"],
    )
    return {
        "context": context,
        "outline": outline,
        "events": [
            {
                "step": "context_assembled",
                "data": {
                    "context": model_data(context),
                },
            },
            {
                "step": "outline_done",
                "data": {
                    "plot_outline": model_data(outline),
                },
            },
        ],
    }


def generate_scene_node(state: GenerationState) -> GenerationState:
    stream_writer = get_stream_writer()
    beat_entity_anchors = build_entity_retrieval_anchors(state["outline"])
    anchors = [
        *state["action_parse_result"].anchors,
        *beat_entity_anchors,
    ]
    with entity_store() as ent_store, npc_profile_store() as npc_store:
        writer_entity_profiles = gather_entity_profiles_from_state_and_anchors(
            current_state=state["current_state"],
            anchors=anchors,
            entity_store=ent_store,
        )
        writer_npc_profiles = gather_npc_profiles_from_state_and_anchors(
            current_state=state["current_state"],
            anchors=anchors,
            npc_store=npc_store,
        )

    writer_entity_profile_views = [
        profile.semantic_view()
        for profile in writer_entity_profiles
    ]
    writer_npc_public_views = [
        profile.public_view()
        for profile in writer_npc_profiles
    ]
    recent_scene = (
        state["short_term_memory"].recent_scenes[-1]
        if state["short_term_memory"].recent_scenes
        else None
    )
    next_turn_index = recent_scene.turn_index + 1 if recent_scene else 1

    scene_result = SceneWriterAgent(gateway_for(state)).write(
        outline=state["outline"],
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        recent_scene=recent_scene,
        turn_index=next_turn_index,
        entity_profiles=writer_entity_profile_views,
        npc_profiles=writer_npc_public_views,
        max_chars=state.get("max_chars", 1500),
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
    return {
        "beat_entity_anchors": beat_entity_anchors,
        "writer_entity_profile_views": writer_entity_profile_views,
        "writer_npc_public_views": writer_npc_public_views,
        "recent_scene": recent_scene,
        "next_turn_index": next_turn_index,
        "scene_result": scene_result,
    }
