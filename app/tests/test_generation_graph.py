from types import SimpleNamespace

from langgraph.graph import END, START, StateGraph

from app.graph import generation_graph, generation_nodes
from app.domain.narrative_canon_query.schemas import NarrativeCanonQuerySet
from app.domain.narrative_rewrite.schemas import RewriteStrategyDecision
from app.graph.generation_events import GenerationEventPresenter
from app.graph.generation_state import GenerationState
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory
from app.runtime.session import PendingTurn, get_campaign_runtime


def test_generation_graph_compiles():
    graph = generation_graph.build_generation_graph()

    assert graph is not None


def test_rephrase_graph_runs_only_composer_and_consistency_tail():
    graph = generation_graph.build_rephrase_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("__start__", "rephrase_narrative") in edges
    assert ("rephrase_narrative", "generate_canon_queries") in edges
    assert ("generate_canon_queries", "retrieve_canon_evidence") in edges
    assert ("retrieve_canon_evidence", "check_narrative_consistency") in edges
    assert ("check_narrative_consistency", "revise_narrative") in edges
    assert not any(target in {
        "parse_action",
        "evaluate_roll_modifiers",
        "resolve_turn",
        "consume_director_events",
        "maybe_director_npc",
    } for _source, target in edges)


def test_feedback_rewrite_graph_keeps_two_composer_paths_separate():
    graph = generation_graph.build_feedback_rewrite_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("__start__", "rewrite_from_source") in edges
    assert ("__start__", "recompose") in edges
    assert ("__start__", "__end__") in edges
    assert ("rewrite_from_source", "generate_canon_queries") in edges
    assert ("recompose", "generate_canon_queries") in edges
    assert ("check_narrative_consistency", "revise_narrative") in edges
    assert not any(target in {
        "parse_action",
        "evaluate_roll_modifiers",
        "resolve_turn",
        "consume_director_events",
        "maybe_director_npc",
    } for _source, target in edges)


def test_feedback_rewrite_graph_accepts_no_rewrite_as_terminal_state():
    decision = RewriteStrategyDecision(
        strategy="no_rewrite",
        reason_code="no_actionable_request",
        reasoning="没有明确修改要求。",
    )

    result = generation_graph.build_feedback_rewrite_graph().invoke({
        "events": [],
        "rewrite_strategy_decision": decision,
    })

    assert result["rewrite_strategy_decision"] == decision


def test_rewrite_stream_callbacks_share_mode_aware_event_projection(monkeypatch):
    streamed = []
    monkeypatch.setattr(generation_nodes, "get_stream_writer", lambda: streamed.append)
    state = {
        "recent_scene": "recent",
        "next_turn_index": 4,
        "trace_id": "trace-rewrite",
    }

    kwargs = generation_nodes._rewrite_runtime_kwargs(
        state,
        rewrite_mode="recompose",
    )
    kwargs["on_stream_start"](2)
    kwargs["on_scene_text_delta"]("新正文")

    assert kwargs["recent_scene"] == "recent"
    assert kwargs["turn_index"] == 4
    assert streamed == [
        {
            "step": "scene_stream_started",
            "data": {
                "attempt": 2,
                "revision": False,
                "rewrite_mode": "recompose",
                "trace_id": "trace-rewrite",
            },
        },
        {
            "step": "scene_text_delta",
            "data": {
                "delta": "新正文",
                "revision": False,
                "rewrite_mode": "recompose",
                "trace_id": "trace-rewrite",
            },
        },
    ]


def test_consumer_composer_and_consistency_checker_run_in_order():
    graph = generation_graph.build_generation_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("resolve_turn", "consume_director_events") in edges
    assert ("consume_director_events", "compose_narrative") in edges
    assert ("maybe_director_npc", "compose_narrative") in edges
    assert ("compose_narrative", "generate_canon_queries") in edges
    assert ("generate_canon_queries", "retrieve_canon_evidence") in edges
    assert ("retrieve_canon_evidence", "check_narrative_consistency") in edges
    assert ("check_narrative_consistency", "__end__") in edges
    assert ("check_narrative_consistency", "revise_narrative") in edges
    assert ("revise_narrative", "__end__") in edges
    assert ("compose_narrative", "check_narrative_consistency") not in edges
    assert ("compose_narrative", "__end__") not in edges
    assert ("extract_context_facts", "consume_director_events") not in edges


def test_consistency_route_revises_only_when_assertions_exist():
    assert generation_graph._route_after_consistency_check({
        "narrative_consistency_review": SimpleNamespace(decision="pass"),
    }) == "pass"
    assert generation_graph._route_after_consistency_check({
        "narrative_consistency_review": SimpleNamespace(decision="revise"),
    }) == "revise"


def test_canon_query_node_uses_one_full_catalog_for_llm_and_retrieval(monkeypatch):
    streamed = []
    captured = {}

    class _Store:
        def __init__(self, profiles):
            self.profiles = profiles

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_all(self):
            return self.profiles

    entity_profile = SimpleNamespace(semantic_view=lambda: "all-entity-view")
    npc_profile = SimpleNamespace(director_view=lambda: "all-npc-view")
    stores = {
        "entity": _Store([entity_profile]),
        "npc_profile": _Store([npc_profile]),
    }

    def _build_catalog(**kwargs):
        captured["catalog_kwargs"] = kwargs
        return []

    class _FakeAgent:
        def __init__(self, _gateway):
            pass

        def generate(self, **kwargs):
            captured["agent_kwargs"] = kwargs
            return NarrativeCanonQuerySet(
                thought_process="本轮没有历史主张。",
                queries=[],
            )

    monkeypatch.setattr(
        generation_nodes,
        "_store_factory",
        lambda _state, name: lambda: stores[name],
    )
    monkeypatch.setattr(generation_nodes, "get_stream_writer", lambda: streamed.append)
    monkeypatch.setattr(generation_nodes, "build_entity_catalog", _build_catalog)
    monkeypatch.setattr(generation_nodes, "NarrativeCanonQueryAgent", _FakeAgent)
    monkeypatch.setattr(generation_nodes, "gateway_for", lambda _state: object())

    result = generation_nodes.generate_canon_queries_node({
        "current_state": SimpleNamespace(player=SimpleNamespace(name="莱尔")),
        "narrative_composition": SimpleNamespace(scene_text="风吹过窗沿。"),
        "trace_id": "trace-canon",
        "composer_entity_profile_views": ["selected-only-entity"],
        "action_npc_director_views": ["selected-only-npc"],
    })

    assert streamed[0]["step"] == "canon_query_started"
    assert captured["catalog_kwargs"]["entity_profiles"] == ["all-entity-view"]
    assert captured["catalog_kwargs"]["npc_profiles"] == ["all-npc-view"]
    assert captured["agent_kwargs"]["entity_profiles"] == ["all-entity-view"]
    assert captured["agent_kwargs"]["npc_profiles"] == ["all-npc-view"]
    assert result["events"][0]["step"] == "canon_queries_generated"


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
            assert stream_mode == ["updates", "custom"]
            yield "custom", {
                "step": "scene_text_delta",
                "data": {"delta": "正文"},
            }
            yield "updates", {
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
        "scene_text_delta",
        "node_done",
        "finalized",
    ]


def test_generation_pipeline_reloads_store_context_on_every_invocation(monkeypatch):
    latest = {"memory_version": "before-maintenance"}
    loaded = []

    class _EmptyGraph:
        def stream(self, _state, stream_mode):
            assert stream_mode == ["updates", "custom"]
            return iter(())

    def _prepare(*_args, **_kwargs):
        value = latest["memory_version"]
        loaded.append(value)
        return {
            "events": [{
                "step": "context_loaded",
                "data": {"memory_version": value},
            }],
        }

    monkeypatch.setattr(generation_graph, "prepare_generation_state", _prepare)
    monkeypatch.setattr(generation_graph, "build_generation_graph", _EmptyGraph)
    monkeypatch.setattr(
        generation_graph,
        "finalize_pending_turn",
        lambda state, **_kwargs: state,
    )

    first = list(generation_graph._generate_turn_stream("观察", "普通", 900))
    latest["memory_version"] = "after-maintenance"
    retried = list(generation_graph._generate_turn_stream("观察", "普通", 900))

    assert loaded == ["before-maintenance", "after-maintenance"]
    assert first[0]["data"]["memory_version"] == "before-maintenance"
    assert retried[0]["data"]["memory_version"] == "after-maintenance"


def test_generation_buffers_context_events_until_action_parsed(monkeypatch):
    class _FakeGraph:
        def stream(self, _state, stream_mode):
            assert stream_mode == ["updates", "custom"]
            yield "updates", {
                "extract_context_facts": {
                    "context_facts": object(),
                    "events": [{"step": "context_facts_done", "data": {}}],
                }
            }
            yield "updates", {
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


def test_reroll_creates_next_version_in_same_group(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "turn_v1"
    group_id = "group_1"
    state_before = object()
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(PendingTurn(
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
    ))
    captured = {}
    order = []

    class _StateStore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load_or_default(self):
            order.append("state_loaded")
            return state_before

    def _retry_stream(user_input, difficulty, max_chars, **kwargs):
        order.append("retry_generation_started")
        captured.update({
            "user_input": user_input,
            "difficulty": difficulty,
            "max_chars": max_chars,
            **kwargs,
        })
        yield {"step": "scene_written", "data": {"turn_id": "turn_v2"}}

    configure_runtime_services(current_state=_StateStore)
    monkeypatch.setattr(generation_graph, "_generate_turn_stream", _retry_stream)

    try:
        events = list(generation_graph.reroll_turn_stream(turn_id))
        old_version_retained = turn_id in runtime.pending_turns
    finally:
        runtime.pending_turns.remove(turn_id)

    assert events[0]["step"] == "reroll_started"
    assert order == [
        "state_loaded",
        "retry_generation_started",
    ]
    assert captured == {
        "user_input": "观察门外",
        "difficulty": "普通",
        "max_chars": 900,
        "group_id": group_id,
        "version_index": 2,
    }
    assert old_version_retained is True
    assert events[-1]["data"]["turn_id"] == "turn_v2"


def test_rephrase_reuses_selected_composer_context_without_full_generation(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "turn_v1"
    group_id = "group_1"
    state_before = object()
    context = SimpleNamespace(turn_resolution=object())
    director_npc_result = object()
    short_term = ShortTermMemory()
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(PendingTurn(
        turn_id=turn_id,
        group_id=group_id,
        version_index=1,
        user_input="观察门外",
        difficulty="普通",
        max_chars=900,
        state_before=state_before,
        context=context,
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="版本一"),
        director_npc_result=director_npc_result,
        turn_index=3,
        short_term_memory=short_term,
    ))
    player_resolution = runtime.pending_turns.get(turn_id).player_resolution
    captured = {}
    graph = object()

    class _StateStore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load_or_default(self):
            return state_before

    def _stream(state, selected_graph, **kwargs):
        captured.update({"state": state, "graph": selected_graph, **kwargs})
        yield {"step": "scene_written", "data": {"turn_id": "turn_v2"}}

    configure_runtime_services(current_state=_StateStore)
    monkeypatch.setattr(generation_graph, "build_rephrase_graph", lambda: graph)
    monkeypatch.setattr(generation_graph, "_stream_generation_graph", _stream)
    monkeypatch.setattr(
        generation_graph,
        "_generate_turn_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("full generation must not run")
        ),
    )

    try:
        events = list(generation_graph.rephrase_turn_stream(turn_id))
    finally:
        runtime.pending_turns.remove(turn_id)

    state = captured["state"]
    assert captured["graph"] is graph
    assert captured["group_id"] == group_id
    assert captured["version_index"] == 2
    assert state["context"] is context
    assert state["turn_resolution"] is context.turn_resolution
    assert state["player_resolution"] is player_resolution
    assert state["current_state"] is state_before
    assert state["short_term_memory"] is short_term
    assert state["director_npc_result"] is director_npc_result
    assert events[-1]["data"]["turn_id"] == "turn_v2"


def test_feedback_no_rewrite_does_not_create_or_stream_a_variant(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "turn_v1"
    state_before = object()
    context = SimpleNamespace(turn_resolution=object())
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(PendingTurn(
        turn_id=turn_id,
        group_id="group_1",
        version_index=1,
        user_input="观察门外",
        difficulty="普通",
        max_chars=900,
        state_before=state_before,
        context=context,
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="版本一"),
        turn_index=3,
        short_term_memory=ShortTermMemory(),
    ))

    class _StateStore:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def load_or_default(self): return state_before

    class _Selector:
        def __init__(self, _gateway): pass
        def select(self, **_kwargs):
            return RewriteStrategyDecision(
                strategy="no_rewrite",
                reason_code="no_actionable_request",
                reasoning="输入不构成修改要求。",
            )

    configure_runtime_services(
        current_state=_StateStore,
        gateway_factory=lambda: SimpleNamespace(
            set_trace_context=lambda **_kwargs: None
        ),
    )
    monkeypatch.setattr(generation_graph, "RewriteStrategySelector", _Selector)
    monkeypatch.setattr(
        generation_graph,
        "_stream_generation_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("no_rewrite must not enter a generation graph")
        ),
    )

    try:
        events = list(generation_graph.feedback_rewrite_stream(
            turn_id,
            source_scene_text="版本一",
            user_feedback="123456",
        ))
        variants = runtime.pending_turns.variants("group_1")
    finally:
        runtime.pending_turns.remove(turn_id)

    assert [event["step"] for event in events] == [
        "feedback_rewrite_started",
        "feedback_rewrite_strategy_selected",
    ]
    assert events[-1]["data"]["strategy"] == "no_rewrite"
    assert len(variants) == 1
