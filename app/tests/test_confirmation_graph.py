from types import SimpleNamespace

from langgraph.graph import END, START, StateGraph

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.graph import confirmation_nodes
from app.graph.confirmation_graph import (
    build_confirmation_first_wave_graph,
    build_confirmation_second_wave_graph,
    capture_confirmation_node_errors,
)
from app.graph.confirmation_helpers import merge_confirmation_node_update
from app.graph.confirmation_state import ConfirmationState


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


def test_node_updates_are_persisted_to_retry_cache_at_merge_boundary():
    marker = object()
    state = {"events": [], "cache": {}, "write_db": True}

    merge_confirmation_node_update(
        state,
        {
            "scene_facts": marker,
            "entity_extraction": marker,
            "events": [{"step": "done", "data": {}}],
        },
    )

    assert state["cache"]["scene_facts"] is marker
    assert state["cache"]["entity_extraction"] is marker
    assert state["events"] == [{"step": "done", "data": {}}]


def test_dry_run_merge_does_not_cache_write_only_results():
    marker = object()
    state = {"events": [], "cache": {}, "write_db": False}

    merge_confirmation_node_update(
        state,
        {
            "entity_extraction": marker,
            "updated_entity_ids": ["entity_1"],
        },
    )

    assert "entity_extraction" not in state["cache"]
    assert "updated_entity_ids" not in state["cache"]


def test_parallel_success_is_cacheable_when_sibling_node_fails():
    marker = object()

    def succeeds(_state: ConfirmationState) -> ConfirmationState:
        return {"action_preview": marker}

    def fails(_state: ConfirmationState) -> ConfirmationState:
        raise RuntimeError("failed")

    graph = StateGraph(ConfirmationState)
    graph.add_node("succeeds", capture_confirmation_node_errors("succeeds", succeeds))
    graph.add_node("fails", capture_confirmation_node_errors("fails", fails))
    graph.add_edge(START, "succeeds")
    graph.add_edge(START, "fails")
    graph.add_edge("succeeds", END)
    graph.add_edge("fails", END)

    state = {"events": [], "node_errors": [], "cache": {}, "write_db": True}
    for update in graph.compile().stream(state, stream_mode="updates"):
        for node_update in update.values():
            merge_confirmation_node_update(state, node_update)

    assert state["cache"]["action_preview"] is marker
    assert state["node_errors"][0]["node"] == "fails"
    assert str(state["node_errors"][0]["exception"]) == "failed"


def test_edited_scene_preview_drops_original_directions_and_uses_scene_facts(
    monkeypatch,
    configure_runtime_services,
):
    captured = {}
    scene_facts = SceneFactSet(scene_focus="编辑后的场景", facts=[])
    scene_result = SceneWriterResult(
        scene_text="原始正文",
        next_action_directions=[
            NextActionDirection(
                direction="继续原稿中的追逐",
                rationale="原稿保留了追逐。",
            )
        ],
    )

    class _Gateway:
        def set_trace_context(self, **_kwargs):
            return None

    class _PreviewAgent:
        def __init__(self, _gateway):
            pass

        def generate(self, **kwargs):
            captured.update(kwargs)
            return {"previews": []}

    services = configure_runtime_services(gateway_factory=_Gateway)
    monkeypatch.setattr(confirmation_nodes, "ActionPreviewAgent", _PreviewAgent)

    state = {
        "pipeline_input": SimpleNamespace(turn_index=1, scene_result=scene_result),
        "runtime_services": services,
        "trace_id": "trace",
        "cache": {},
        "effective_scene_text": "编辑后的正文",
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene_001", location="新地点"),
            player=PlayerState(name="主角"),
        ),
        "core_memory": CoreMemory(items=[]),
        "recent_summaries": [],
        "scene_facts": scene_facts,
    }

    confirmation_nodes.action_preview_node(state)

    assert captured["next_action_directions"] == []
    assert captured["scene_facts"] is scene_facts
