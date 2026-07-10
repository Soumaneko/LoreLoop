from langgraph.graph import END, START, StateGraph

from app.graph import generation_graph
from app.graph.generation_state import GenerationState


def test_generation_graph_compiles():
    graph = generation_graph.build_generation_graph()

    assert graph is not None


def test_generation_events_reducer_merges_parallel_branch_events():
    def left(_state: GenerationState) -> GenerationState:
        return {"events": [{"step": "left", "data": {}}]}

    def right(_state: GenerationState) -> GenerationState:
        return {"events": [{"step": "right", "data": {}}]}

    graph = StateGraph(GenerationState)
    graph.add_node("left", left)
    graph.add_node("right", right)
    graph.add_edge(START, "left")
    graph.add_edge(START, "right")
    graph.add_edge("left", END)
    graph.add_edge("right", END)

    result = graph.compile().invoke({"events": [{"step": "start", "data": {}}]})

    assert [event["step"] for event in result["events"]] == [
        "start",
        "left",
        "right",
    ]


def test_stream_generation_turn_forwards_state_events(monkeypatch):
    class _FakeGraph:
        def stream(self, _state, stream_mode):
            assert stream_mode == "updates"
            yield {
                "fake_node": {
                    "value": 1,
                    "events": [{"step": "node_done", "data": {"value": 1}}],
                }
            }

    monkeypatch.setattr(
        generation_graph,
        "prepare_generation_state",
        lambda *_args, **_kwargs: {
            "events": [{"step": "prepared", "data": {}}],
        },
    )
    monkeypatch.setattr(generation_graph, "build_generation_graph", lambda: _FakeGraph())
    monkeypatch.setattr(
        generation_graph,
        "finalize_pending_turn",
        lambda state: {
            **state,
            "events": [
                *state["events"],
                {"step": "finalized", "data": {}},
            ],
        },
    )

    events = list(generation_graph.stream_generation_turn("观察"))

    assert [event["step"] for event in events] == [
        "prepared",
        "node_done",
        "finalized",
    ]


def test_stream_generation_buffers_context_events_until_action_parsed(monkeypatch):
    class _FakeGraph:
        def stream(self, _state, stream_mode):
            assert stream_mode == "updates"
            yield {
                "extract_context_facts": {
                    "context_facts": object(),
                    "events": [{"step": "context_facts_done", "data": {}}],
                }
            }
            yield {
                "parse_action": {
                    "action_parse_result": object(),
                    "events": [
                        {"step": "action_parsed", "data": {}},
                        {"step": "retrieval_done", "data": {}},
                    ],
                }
            }

    monkeypatch.setattr(
        generation_graph,
        "prepare_generation_state",
        lambda *_args, **_kwargs: {
            "events": [{"step": "prepared", "data": {}}],
        },
    )
    monkeypatch.setattr(generation_graph, "build_generation_graph", lambda: _FakeGraph())
    monkeypatch.setattr(
        generation_graph,
        "finalize_pending_turn",
        lambda state: {
            **state,
            "events": [
                *state["events"],
                {"step": "finalized", "data": {}},
            ],
        },
    )

    events = list(generation_graph.stream_generation_turn("观察"))

    assert [event["step"] for event in events] == [
        "prepared",
        "action_parsed",
        "context_facts_done",
        "retrieval_done",
        "finalized",
    ]
