from langgraph.graph import END, START, StateGraph

from app.graph.confirmation_graph import (
    build_confirmation_first_wave_graph,
    build_confirmation_graph,
    build_confirmation_second_wave_graph,
)
from app.graph.confirmation_state import ConfirmationState


def test_confirmation_graph_compiles():
    first_wave, second_wave = build_confirmation_graph()
    assert first_wave is not None
    assert second_wave is not None


def test_confirmation_first_wave_preserves_parallel_extract_dependencies():
    graph = build_confirmation_first_wave_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("scene_fact_extract", "state_update") in edges
    assert ("scene_fact_extract", "memory_draft_extract") in edges
    assert ("scene_fact_extract", "entity_extract") in edges
    assert ("scene_fact_extract", "npc_profile_extract") in edges


def test_confirmation_second_wave_starts_after_helper_state_is_ready():
    graph = build_confirmation_second_wave_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("__start__", "action_preview") in edges
    assert ("__start__", "director_event_update") in edges
    assert ("__start__", "entity_profile_update") in edges
    assert ("__start__", "npc_profile_update") in edges


def test_confirmation_events_reducer_appends_node_events():
    def first(_state: ConfirmationState) -> ConfirmationState:
        return {"events": [{"step": "first", "data": {}}]}

    def second(_state: ConfirmationState) -> ConfirmationState:
        return {"events": [{"step": "second", "data": {}}]}

    graph = StateGraph(ConfirmationState)
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    result = graph.compile().invoke({"events": [{"step": "start", "data": {}}]})

    assert [event["step"] for event in result["events"]] == [
        "start",
        "first",
        "second",
    ]
