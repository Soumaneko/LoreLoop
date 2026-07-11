from types import SimpleNamespace

from langgraph.graph import END, START, StateGraph

from app.graph import generation_graph
from app.graph.generation_events import GenerationEventPresenter
from app.graph.generation_state import GenerationState
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory
from app.runtime.session import PENDING_TURNS, PendingTurn


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


def test_generate_turn_stream_forwards_state_events(monkeypatch):
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
        lambda state, **_kwargs: {
            **state,
            "events": [
                *state["events"],
                {"step": "finalized", "data": {}},
            ],
        },
    )

    events = list(generation_graph.generate_turn_stream("观察"))

    assert [event["step"] for event in events] == [
        "prepared",
        "node_done",
        "finalized",
    ]


def test_generation_buffers_context_events_until_action_parsed(monkeypatch):
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
        lambda state, **_kwargs: {
            **state,
            "events": [
                *state["events"],
                {"step": "finalized", "data": {}},
            ],
        },
    )

    events = list(generation_graph.generate_turn_stream("观察"))

    assert [event["step"] for event in events] == [
        "prepared",
        "action_parsed",
        "context_facts_done",
        "retrieval_done",
        "finalized",
    ]


def test_generation_event_presenter_flushes_context_before_later_nodes():
    presenter = GenerationEventPresenter()

    assert presenter.present(
        "extract_context_facts",
        [{"step": "context_facts_done", "data": {}}],
    ) == []
    assert [event["step"] for event in presenter.present(
        "parse_action",
        [
            {"step": "action_parsed", "data": {}},
            {"step": "retrieval_done", "data": {}},
        ],
    )] == ["action_parsed", "context_facts_done", "retrieval_done"]
    assert presenter.finish() == []


def test_retry_creates_next_version_in_same_group(monkeypatch):
    turn_id = "turn_v1"
    group_id = "group_1"
    state_before = object()
    PENDING_TURNS[turn_id] = PendingTurn(
        turn_id=turn_id,
        group_id=group_id,
        version_index=1,
        user_input="观察门外",
        difficulty="普通",
        max_chars=900,
        state_before=state_before,
        context=SimpleNamespace(),
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="版本一"),
        turn_index=3,
        short_term_memory=ShortTermMemory(),
    )
    captured = {}

    class _StateStore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load_or_default(self):
            return state_before

    def _retry_stream(user_input, difficulty, max_chars, **kwargs):
        captured.update({
            "user_input": user_input,
            "difficulty": difficulty,
            "max_chars": max_chars,
            **kwargs,
        })
        yield {"step": "scene_written", "data": {"turn_id": "turn_v2"}}

    monkeypatch.setattr(generation_graph, "current_state_store", _StateStore)
    monkeypatch.setattr(generation_graph, "_generate_turn_stream", _retry_stream)

    try:
        events = list(generation_graph.retry_turn_stream(turn_id))
        old_version_retained = turn_id in PENDING_TURNS
    finally:
        PENDING_TURNS.pop(turn_id, None)

    assert events[0]["step"] == "retry_started"
    assert captured == {
        "user_input": "观察门外",
        "difficulty": "普通",
        "max_chars": 900,
        "group_id": group_id,
        "version_index": 2,
    }
    assert old_version_retained is True
    assert events[-1]["data"]["turn_id"] == "turn_v2"
