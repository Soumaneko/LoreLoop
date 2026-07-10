from __future__ import annotations

from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.roll.resolver import RollResolver
from app.domain.roll.schemas import RollConfig
from app.engine.serialization import model_data
from app.engine.stores import (
    director_npc_store,
    entity_store,
    memory_store,
    npc_profile_store,
)
from app.workflows.context_assembler import assemble_context
from app.workflows.director_npc import maybe_director_npc_workflow
from app.workflows.evaluate_roll_modifiers import evaluate_roll_modifiers_workflow
from app.workflows.extract_context_facts import extract_context_facts_workflow
from app.workflows.generate_plot_outline import generate_plot_outline_workflow
from app.workflows.generate_scene import (
    build_entity_retrieval_anchors,
    generate_scene_workflow,
)
from app.workflows.resolve_rolls import resolve_rolls_workflow
from app.workflows.resolve_turn import resolve_turn_workflow
from app.workflows.retrieve_candidates import retrieve_candidates_workflow
from app.workflows.update_entity_profiles import gather_entity_profiles_from_state_and_anchors
from app.domain.npc_profile.workflows import gather_npc_profiles_from_state_and_anchors

from .generation_helpers import gateway_for
from .generation_state import GenerationState


def extract_context_facts_node(state: GenerationState) -> GenerationState:
    try:
        with memory_store() as mem_store:
            context_facts, context_fact_memories = extract_context_facts_workflow(
                user_input=state["user_input"],
                core_memory=state["core_memory"],
                current_state=state["current_state"],
                scene_summaries=state["scene_summaries"],
                memory_store=mem_store,
                anchors=state.get("matched_anchors", []),
                entity_profiles=state.get("action_entity_profile_views", []),
                npc_profiles=state.get("action_npc_director_views", []),
                gateway=gateway_for(state),
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
        retrieved_memories = retrieve_candidates_workflow(
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


def evaluate_roll_modifiers_node(state: GenerationState) -> GenerationState:
    roll_config = RollConfig(seed=None, difficulty=state.get("difficulty", "困难"))
    roll_resolver = RollResolver(roll_config)
    roll_actions = roll_resolver.select_roll_actions(state["action_parse_result"])
    modifier_batch = evaluate_roll_modifiers_workflow(
        state["action_parse_result"],
        state["current_state"],
        gateway_for(state),
        core_memory=state["core_memory"],
        roll_actions=roll_actions,
        trace_id=state["trace_id"],
    )
    roll_results = resolve_rolls_workflow(
        state["action_parse_result"],
        config=roll_config,
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
    turn_resolution = resolve_turn_workflow(
        user_input=state["user_input"],
        current_state=state["current_state"],
        action_parse_result=state["action_parse_result"],
        roll_results=state["roll_results"],
        core_memory=state["core_memory"],
        context_facts=state["context_facts"],
        entity_profiles=state.get("action_entity_profile_views", []),
        npc_profiles=state.get("action_npc_director_views", []),
        gateway=gateway_for(state),
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
    with director_npc_store() as npc_store, npc_profile_store() as profile_store:
        director_npc_result = maybe_director_npc_workflow(
            core_memory=state["core_memory"],
            current_state=state["current_state"],
            recent_summaries=state.get("recent_summaries", []),
            director_event=state.get("active_director_event"),
            user_input=state["user_input"],
            turn_index=state["turn_index"],
            director_npc_store=npc_store,
            npc_profile_store=profile_store,
            creator_gateway=gateway_for(state),
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
        action_parse_result=state["action_parse_result"],
        turn_resolution=state["turn_resolution"],
        core_memory=state["core_memory"],
        context_facts=state["context_facts"],
        entity_profiles=state.get("action_entity_profile_views", []),
        npc_profiles=state.get("action_npc_director_views", []),
        director_event=state.get("active_director_event"),
        recent_resolved_director_event=state.get("recent_resolved_director_event"),
        director_npc_injection=(
            director_npc_result.plot_injection
            if director_npc_result is not None
            else None
        ),
    )
    outline = generate_plot_outline_workflow(
        context=context,
        gateway=gateway_for(state),
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

    scene_result = generate_scene_workflow(
        outline=state["outline"],
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        context_facts=state["context_facts"],
        short_term_memory=state["short_term_memory"],
        gateway=gateway_for(state),
        turn_index=next_turn_index,
        entity_profiles=writer_entity_profile_views,
        npc_profiles=writer_npc_public_views,
        max_chars=state.get("max_chars", 1500),
        trace_id=state["trace_id"],
    )
    return {
        "beat_entity_anchors": beat_entity_anchors,
        "writer_entity_profile_views": writer_entity_profile_views,
        "writer_npc_public_views": writer_npc_public_views,
        "recent_scene": recent_scene,
        "next_turn_index": next_turn_index,
        "scene_result": scene_result,
    }
