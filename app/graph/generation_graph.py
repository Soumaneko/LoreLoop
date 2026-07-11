from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.domain.roll.schemas import Difficulty

from .generation_helpers import (
    finalize_pending_turn,
    prepare_generation_state,
)
from .generation_events import GenerationEventPresenter
from .generation_nodes import (
    evaluate_roll_modifiers_node,
    extract_context_facts_node,
    generate_plot_outline_node,
    generate_scene_node,
    maybe_director_npc_node,
    parse_action_node,
    resolve_turn_node,
)
from .generation_state import GenerationEvent, GenerationState


def build_generation_graph():
    graph = StateGraph(GenerationState)
    graph.add_node("extract_context_facts", extract_context_facts_node)
    graph.add_node("parse_action", parse_action_node)
    graph.add_node("evaluate_roll_modifiers", evaluate_roll_modifiers_node)
    graph.add_node("resolve_turn", resolve_turn_node)
    graph.add_node("maybe_director_npc", maybe_director_npc_node)
    graph.add_node("generate_plot_outline", generate_plot_outline_node)
    graph.add_node("generate_scene", generate_scene_node)

    graph.add_edge(START, "extract_context_facts")
    graph.add_edge(START, "parse_action")
    graph.add_edge("parse_action", "evaluate_roll_modifiers")
    graph.add_edge(["extract_context_facts", "evaluate_roll_modifiers"], "resolve_turn")
    graph.add_edge("resolve_turn", "maybe_director_npc")
    graph.add_edge("maybe_director_npc", "generate_plot_outline")
    graph.add_edge("generate_plot_outline", "generate_scene")
    graph.add_edge("generate_scene", END)

    return graph.compile()


def generate_turn_stream(
    user_input: str,
    difficulty: Difficulty = "困难",
    max_chars: int = 1500,
):
    state: dict[str, Any] = prepare_generation_state(user_input, difficulty, max_chars)
    event_count = 0
    for event in state.get("events", []):
        event_count += 1
        yield event

    graph = build_generation_graph()
    event_presenter = GenerationEventPresenter()

    def append_and_yield(event: GenerationEvent):
        nonlocal event_count
        state["events"] = [*state.get("events", []), event]
        event_count += 1
        yield event

    for update in graph.stream(state, stream_mode="updates"):
        for node_name, node_update in update.items():
            events = node_update.get("events", [])
            state.update({
                key: value
                for key, value in node_update.items()
                if key != "events"
            })

            for event in event_presenter.present(node_name, events):
                yield from append_and_yield(event)

    for event in event_presenter.finish():
        yield from append_and_yield(event)

    state = finalize_pending_turn(state)
    for event in state.get("events", [])[event_count:]:
        event_count += 1
        yield event
