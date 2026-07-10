from __future__ import annotations

import uuid
from typing import Any

from app.core.llm.config import create_gateway
from app.domain.roll.schemas import Difficulty
from app.engine.serialization import model_data
from app.engine.session import PENDING_TURNS, PendingTurn
from app.engine.stores import (
    core_memory_store,
    current_state_store,
    director_event_store,
    entity_store,
    npc_profile_store,
    scene_summary_store,
    short_term_memory_store,
)
from app.workflows.select_context_profiles import select_context_profiles
from app.workflows.update_director_event import DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS

from .generation_state import GenerationEvent, GenerationState


def gateway_for(state: GenerationState):
    gateway = create_gateway()
    turn_index = state.get("turn_index") or state.get("next_turn_index")
    if turn_index is not None:
        gateway.set_trace_context(turn_index=turn_index, session_type="generation")
    return gateway


def prepare_generation_state(
    user_input: str,
    difficulty: Difficulty = "困难",
    max_chars: int = 1500,
) -> GenerationState:
    trace_id = str(uuid.uuid4())
    core_memory = core_memory_store().load_required()

    with current_state_store() as state_store:
        current_state = state_store.load_or_default()

    with short_term_memory_store() as short_store:
        short_term_memory = short_store.get_recent()

    turn_index = (
        short_term_memory.recent_scenes[-1].turn_index + 1
        if short_term_memory.recent_scenes
        else 1
    )

    with scene_summary_store() as summary_store:
        scene_summaries = summary_store.list_active()
        recent_summaries = summary_store.get_recent(5)

    with director_event_store() as director_store:
        active_director_event = director_store.get_active()
        recent_resolved_director_event = (
            None
            if active_director_event is not None
            else director_store.get_recent_resolved(
                turn_index=turn_index,
                cooldown_turns=DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS,
            )
        )

    state: GenerationState = {
        "events": [],
        "user_input": user_input,
        "difficulty": difficulty,
        "max_chars": max_chars,
        "trace_id": trace_id,
        "core_memory": core_memory,
        "current_state": current_state,
        "short_term_memory": short_term_memory,
        "turn_index": turn_index,
        "next_turn_index": turn_index,
        "scene_summaries": scene_summaries,
        "recent_summaries": recent_summaries,
        "active_director_event": active_director_event,
        "recent_resolved_director_event": recent_resolved_director_event,
    }

    with entity_store() as ent_store, npc_profile_store() as npc_store:
        selection = select_context_profiles(
            user_input=user_input,
            current_state=current_state,
            entity_store=ent_store,
            npc_store=npc_store,
        )

    state.update({
        "matched_anchors": selection.anchors,
        "action_entity_profile_views": [
            profile.semantic_view()
            for profile in selection.entity_profiles
        ],
        "action_npc_director_views": [
            profile.director_view()
            for profile in selection.npc_profiles
        ],
    })
    state["events"] = [
        state_loaded_event(state),
        profiles_loaded_event(state),
    ]
    return state


def finalize_pending_turn(state: GenerationState) -> GenerationState:
    turn_id = str(uuid.uuid4())
    PENDING_TURNS[turn_id] = PendingTurn(
        turn_id=turn_id,
        user_input=state["user_input"],
        state_before=state["current_state"],
        context=state["context"],
        outline=state["outline"],
        scene_result=state["scene_result"],
        director_npc_result=state.get("director_npc_result"),
        turn_index=state["next_turn_index"],
        short_term_memory=state["short_term_memory"],
    )
    finalized = {
        **state,
        "turn_id": turn_id,
        "turn_index": state["next_turn_index"],
        "status": "awaiting_confirm",
        "pending_turn_status": "awaiting_confirm",
    }
    finalized["events"] = [
        *state.get("events", []),
        scene_written_event(finalized),
    ]
    return finalized


def merge_node_update(state: dict[str, Any], node_update: dict[str, Any]) -> list[GenerationEvent]:
    events = node_update.get("events", [])
    state.update({
        key: value
        for key, value in node_update.items()
        if key != "events"
    })
    state["events"] = [*state.get("events", []), *events]
    return events


def state_loaded_event(state: dict[str, Any]) -> GenerationEvent:
    return {
        "step": "state_loaded",
        "data": {
            "trace_id": state["trace_id"],
            "state_before": model_data(state["current_state"]),
            "core_memory": model_data(state["core_memory"]),
            "short_term_memory": model_data(state["short_term_memory"]),
            "scene_summaries": model_data(state["scene_summaries"]),
            "recent_summaries": model_data(state["recent_summaries"]),
            "active_director_event": model_data(state["active_director_event"]),
            "recent_resolved_director_event": model_data(
                state["recent_resolved_director_event"]
            ),
        },
    }


def profiles_loaded_event(state: dict[str, Any]) -> GenerationEvent:
    return {
        "step": "entity_profiles_loaded",
        "data": {
            "anchors": model_data(state.get("matched_anchors", [])),
            "entity_profiles": model_data(
                state.get("action_entity_profile_views", [])
            ),
            "npc_profiles": model_data(
                state.get("action_npc_director_views", [])
            ),
        },
    }


def scene_written_event(state: dict[str, Any]) -> GenerationEvent:
    return {
        "step": "scene_written",
        "data": {
            "turn_id": state["turn_id"],
            "trace_id": state["trace_id"],
            "turn_index": state["turn_index"],
            "writer_entity_profiles": model_data(
                state.get("writer_entity_profile_views", [])
            ),
            "writer_npc_profiles": model_data(
                state.get("writer_npc_public_views", [])
            ),
            "recent_scene": model_data(state.get("recent_scene")),
            "scene_result": model_data(state["scene_result"]),
            "context": model_data(state["context"]),
            "status": "awaiting_confirm",
        },
    }
