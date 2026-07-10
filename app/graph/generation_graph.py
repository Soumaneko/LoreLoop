from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.domain.roll.schemas import Difficulty

from .generation_helpers import (
    finalize_pending_turn,
    prepare_generation_state,
)
from .generation_nodes import (
    evaluate_roll_modifiers_node,
    extract_context_facts_node,
    generate_plot_outline_node,
    generate_scene_node,
    maybe_director_npc_node,
    parse_action_node,
    resolve_turn_node,
)
from .generation_state import GenerationState


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
    context_events: list[dict[str, Any]] = []
    action_events_started = False

    def append_and_yield(event: dict[str, Any]):
        nonlocal event_count
        state["events"] = [*state.get("events", []), event]
        event_count += 1
        yield event

    def drain_context_events():
        while context_events:
            yield from append_and_yield(context_events.pop(0))

    for update in graph.stream(state, stream_mode="updates"):
        for node_name, node_update in update.items():
            events = node_update.get("events", [])
            state.update({
                key: value
                for key, value in node_update.items()
                if key != "events"
            })

            if node_name == "extract_context_facts":
                context_events.extend(events)
                if action_events_started:
                    yield from drain_context_events()
                continue

            for event in events:
                if event.get("step") == "action_parsed":
                    action_events_started = True
                    yield from append_and_yield(event)
                    yield from drain_context_events()
                    continue
                if context_events and event.get("step") not in {
                    "retrieval_done",
                    "modifiers_done",
                    "rolls_done",
                }:
                    yield from drain_context_events()
                yield from append_and_yield(event)

    yield from drain_context_events()

    state = finalize_pending_turn(state)
    for event in state.get("events", [])[event_count:]:
        event_count += 1
        yield event
