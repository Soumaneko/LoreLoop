from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .confirmation_helpers import (
    finalize_confirmation_state,
    merge_confirmation_node_update,
    prepare_confirmation_second_wave,
    prepare_confirmation_state,
)
from .confirmation_nodes import (
    action_preview_node,
    director_event_update_node,
    entity_extract_node,
    entity_profile_update_node,
    memory_draft_extract_node,
    npc_profile_extract_node,
    npc_profile_update_node,
    scene_fact_extract_node,
    state_update_node,
)
from .confirmation_state import ConfirmationState


def build_confirmation_first_wave_graph():
    graph = StateGraph(ConfirmationState)
    graph.add_node("scene_fact_extract", scene_fact_extract_node)
    graph.add_node("state_update", state_update_node)
    graph.add_node("memory_draft_extract", memory_draft_extract_node)
    graph.add_node("entity_extract", entity_extract_node)
    graph.add_node("npc_profile_extract", npc_profile_extract_node)

    graph.add_edge(START, "scene_fact_extract")
    graph.add_edge("scene_fact_extract", "state_update")
    graph.add_edge("scene_fact_extract", "memory_draft_extract")
    graph.add_edge("scene_fact_extract", "entity_extract")
    graph.add_edge("scene_fact_extract", "npc_profile_extract")
    graph.add_edge("state_update", END)
    graph.add_edge("memory_draft_extract", END)
    graph.add_edge("entity_extract", END)
    graph.add_edge("npc_profile_extract", END)

    return graph.compile()


def build_confirmation_second_wave_graph():
    graph = StateGraph(ConfirmationState)
    graph.add_node("action_preview", action_preview_node)
    graph.add_node("director_event_update", director_event_update_node)
    graph.add_node("entity_profile_update", entity_profile_update_node)
    graph.add_node("npc_profile_update", npc_profile_update_node)

    graph.add_edge(START, "action_preview")
    graph.add_edge(START, "director_event_update")
    graph.add_edge(START, "entity_profile_update")
    graph.add_edge(START, "npc_profile_update")
    graph.add_edge("action_preview", END)
    graph.add_edge("director_event_update", END)
    graph.add_edge("entity_profile_update", END)
    graph.add_edge("npc_profile_update", END)

    return graph.compile()


def build_confirmation_graph():
    first_wave = build_confirmation_first_wave_graph()
    second_wave = build_confirmation_second_wave_graph()
    return first_wave, second_wave


def stream_confirmation_pipeline(
    pipeline_input: Any,
    *,
    write_db: bool = True,
    scene_text: str | None = None,
):
    state: dict[str, Any] = prepare_confirmation_state(
        pipeline_input,
        write_db=write_db,
        scene_text=scene_text,
    )
    event_count = 0
    for event in state.get("events", []):
        event_count += 1
        yield event

    first_wave_graph, second_wave_graph = build_confirmation_graph()
    for update in first_wave_graph.stream(state, stream_mode="updates"):
        for node_update in update.values():
            for event in merge_confirmation_node_update(state, node_update):
                event_count += 1
                yield event

    state = prepare_confirmation_second_wave(state)
    for event in state.get("events", [])[event_count:]:
        event_count += 1
        yield event

    for update in second_wave_graph.stream(state, stream_mode="updates"):
        for node_update in update.values():
            for event in merge_confirmation_node_update(state, node_update):
                event_count += 1
                yield event

    state = finalize_confirmation_state(state)
    for event in state.get("events", [])[event_count:]:
        event_count += 1
        yield event
