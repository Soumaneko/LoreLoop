from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .initialization_helpers import (
    finalize_initialization_state,
    initialization_result,
    initialization_route,
    prepare_initialization_state,
)
from .initialization_nodes import opening_scene_node, story_direction_node
from .initialization_state import InitializationState


def build_initialization_graph():
    graph = StateGraph(InitializationState)
    graph.add_node("story_direction", story_direction_node)
    graph.add_node("opening_scene", opening_scene_node)

    graph.add_conditional_edges(
        START,
        initialization_route,
        {
            "story_direction": "story_direction",
            "opening_scene": "opening_scene",
        },
    )
    graph.add_edge("story_direction", "opening_scene")
    graph.add_edge("opening_scene", END)
    return graph.compile()


def apply_core_memory_init(payload: dict[str, Any]) -> dict[str, Any]:
    state = prepare_initialization_state(payload)
    graph_state = build_initialization_graph().invoke(state)
    finalized = finalize_initialization_state(graph_state)
    return initialization_result(finalized)
