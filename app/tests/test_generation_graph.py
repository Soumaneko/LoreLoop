from types import SimpleNamespace

import json

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
    graph = generation_graph.build_generation_graph(
        director_is_enabled=True,
        canon_validation_is_enabled=True,
    )

    assert graph is not None


def test_rephrase_graph_runs_only_composer_and_consistency_tail():
    graph = generation_graph.build_rephrase_graph(
        canon_validation_is_enabled=True,
    ).get_graph()
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
    graph = generation_graph.build_feedback_rewrite_graph(
        canon_validation_is_enabled=True,
    ).get_graph()
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

    result = generation_graph.build_feedback_rewrite_graph(
        canon_validation_is_enabled=True,
    ).invoke({
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


def test_director_agent_composer_and_consistency_checker_run_in_order():
    graph = generation_graph.build_generation_graph(
        director_is_enabled=True,
        canon_validation_is_enabled=True,
    ).get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("resolve_turn", "director_agent_stage") in edges
    assert ("director_agent_stage", "compose_narrative") in edges
    assert ("compose_narrative", "generate_canon_queries") in edges
    assert ("generate_canon_queries", "retrieve_canon_evidence") in edges
    assert ("retrieve_canon_evidence", "check_narrative_consistency") in edges
    assert ("check_narrative_consistency", "__end__") in edges
    assert ("check_narrative_consistency", "revise_narrative") in edges
    assert ("revise_narrative", "__end__") in edges
    assert ("compose_narrative", "check_narrative_consistency") not in edges
    assert ("compose_narrative", "__end__") not in edges
    assert ("extract_context_facts", "director_agent_stage") not in edges


def test_compact_generation_graph_skips_director_and_canon_nodes():
    graph = generation_graph.build_generation_graph(
        director_is_enabled=False,
        canon_validation_is_enabled=False,
    ).get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    node_ids = set(graph.nodes)

    assert ("resolve_turn", "compose_narrative") in edges
    assert ("compose_narrative", "__end__") in edges
    assert not {
        "consume_director_events",
        "maybe_director_npc",
        "generate_canon_queries",
        "retrieve_canon_evidence",
        "check_narrative_consistency",
        "revise_narrative",
    } & node_ids


def test_compact_rewrite_graphs_skip_canon_tail():
    rephrase = generation_graph.build_rephrase_graph(
        canon_validation_is_enabled=False,
    ).get_graph()
    feedback = generation_graph.build_feedback_rewrite_graph(
        canon_validation_is_enabled=False,
    ).get_graph()
    rephrase_edges = {(edge.source, edge.target) for edge in rephrase.edges}
    feedback_edges = {(edge.source, edge.target) for edge in feedback.edges}

    assert ("rephrase_narrative", "__end__") in rephrase_edges
    assert ("rewrite_from_source", "__end__") in feedback_edges
    assert ("recompose", "__end__") in feedback_edges
    assert not any(
        edge[1] == "generate_canon_queries"
        for edge in [*rephrase_edges, *feedback_edges]
    )


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

    npc_profile = SimpleNamespace(director_view=lambda: "all-npc-view")
    stores = {
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
        "action_npc_director_views": ["selected-only-npc"],
    })

    assert streamed[0]["step"] == "canon_query_started"
    assert captured["catalog_kwargs"]["npc_profiles"] == ["all-npc-view"]
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
    monkeypatch.setattr(
        generation_graph,
        "build_generation_graph",
        lambda **_kwargs: _FakeGraph(),
    )
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
    monkeypatch.setattr(
        generation_graph,
        "build_generation_graph",
        lambda **_kwargs: _EmptyGraph(),
    )
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
    monkeypatch.setattr(
        generation_graph,
        "build_generation_graph",
        lambda **_kwargs: _FakeGraph(),
    )
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
        ],
    )] == ["action_parsed", "context_facts_done"]
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
    director_stage = object()
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
        director_stage=director_stage,
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
    monkeypatch.setattr(
        generation_graph,
        "build_rephrase_graph",
        lambda **_kwargs: graph,
    )
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
    assert state["director_stage"] is director_stage
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


def test_feedback_rewrite_retains_started_event_in_generation_state(
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
                strategy="revise_from_source",
                reason_code="localized_correction",
                reasoning="局部修改。",
                rewrite_requirements=["收紧最后一句"],
            )

    captured = {}

    def _stream(state, _graph, **kwargs):
        captured["steps"] = [event["step"] for event in state["events"]]
        captured["emitted_event_count"] = kwargs["emitted_event_count"]
        yield from state["events"][kwargs["emitted_event_count"]:]

    configure_runtime_services(
        current_state=_StateStore,
        gateway_factory=lambda: SimpleNamespace(
            set_trace_context=lambda **_kwargs: None
        ),
    )
    monkeypatch.setattr(generation_graph, "RewriteStrategySelector", _Selector)
    monkeypatch.setattr(generation_graph, "_stream_generation_graph", _stream)
    monkeypatch.setattr(
        generation_graph,
        "build_feedback_rewrite_graph",
        lambda **_kwargs: object(),
    )

    try:
        events = list(generation_graph.feedback_rewrite_stream(
            turn_id,
            source_scene_text="版本一",
            user_feedback="收紧最后一句",
        ))
    finally:
        runtime.pending_turns.remove(turn_id)

    assert captured == {
        "steps": [
            "feedback_rewrite_started",
            "feedback_rewrite_strategy_selected",
        ],
        "emitted_event_count": 1,
    }
    assert [event["step"] for event in events] == [
        "feedback_rewrite_started",
        "feedback_rewrite_strategy_selected",
    ]


def test_director_agent_stage_node_passes_select_plus_context_fact_plus_event_profiles(
    tmp_path,
    monkeypatch,
):
    from app.domain.context_facts.schemas import ContextFact, ContextFactSet
    from app.domain.core_memory.schemas import CoreMemory
    from app.domain.current_state import (
        CurrentState,
        PlayerState,
        ResourcesState,
        SceneState,
        WorldClock,
    )
    from app.domain.director import DirectorResourceRef
    from app.domain.director.schemas import DirectorDecisionBatch
    from app.domain.director_event.schemas import DirectorActorRef, DirectorEvent
    from app.domain.npc_profile.schemas import NPCProfile
    from app.domain.turn_resolution.schemas import TurnResolutionResult
    from app.runtime.paths import CampaignPaths
    from app.runtime.stores import StoreBundle

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    base = NPCProfile(name="码头常客")
    fact_hit = NPCProfile(name="柴行简")
    unrelated = NPCProfile(name="无关路人")
    event_hit = NPCProfile(name="林澈")
    with stores.npc_profile() as store:
        for profile in (base, fact_hit, unrelated, event_hit):
            store.upsert(profile)
    with stores.director_event() as event_store:
        event_store.save(DirectorEvent(
            role="arc",
            title="商路封锁",
            subject="河务署",
            intent="控制渡河通道",
            method="封锁旧桥",
            macro_intent="改变商路格局",
            deadline_clock=WorldClock().advance_phase(3),
            created_turn=1,
            updated_turn=1,
            actors=[
                DirectorActorRef(
                    name="林澈",
                    ref=DirectorResourceRef(
                        kind="npc",
                        resource_id=event_hit.npc_id,
                    ),
                ),
            ],
        ))

    captured = {}

    class _FakeExecutor:
        def __init__(self, gateway=None, stores=None, **kwargs):
            self._kwargs = kwargs

        def run(self):
            captured["npc_profiles"] = self._kwargs.get("npc_profiles")
            return (
                SimpleNamespace(),
                DirectorDecisionBatch(reasoning="本轮无需导演介入。", requests=[]),
            )

    monkeypatch.setattr(generation_nodes, "DirectorGenerationExecutor", _FakeExecutor)

    state = {
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: object(),
        ),
        "user_input": "观察码头。",
        "core_memory": CoreMemory(items=[]),
        "current_state": CurrentState(
            scene=SceneState(scene_id="scene_1", location="码头", participants=[]),
            player=PlayerState(name="萧云痕"),
            resources=ResourcesState(inventory=[]),
        ),
        "context_facts": ContextFactSet(
            turn_focus="围绕昨夜商路的事展开。",
            facts=[ContextFact(
                fact_id="f1",
                fact="柴行简的商队昨夜通过了旧桥。",
            )],
        ),
        "recent_summaries": [],
        "turn_index": 3,
        "trace_id": "trace-gen",
        "turn_resolution": TurnResolutionResult(),
        "touched_event_candidates": [],
        "action_npc_profiles": [base],
        "context_fact_npc_profiles": [fact_hit],
    }

    generation_nodes.director_agent_stage_node(state)

    observed = {profile.name for profile in captured["npc_profiles"]}
    assert observed == {"码头常客", "柴行简", "林澈"}
    assert "无关路人" not in observed


def test_compose_narrative_uses_select_plus_context_fact_profiles_only(
    tmp_path,
    monkeypatch,
):
    from app.domain.context_facts.schemas import ContextFact, ContextFactSet
    from app.domain.core_memory.schemas import CoreMemory
    from app.domain.current_state import (
        CurrentState,
        PlayerState,
        ResourcesState,
        SceneState,
        WorldClock,
    )
    from app.domain.director import DirectorResourceRef
    from app.domain.director_event.schemas import DirectorActorRef, DirectorEvent
    from app.domain.npc_profile.schemas import NPCProfile
    from app.domain.turn_resolution.schemas import TurnResolutionResult
    from app.runtime.paths import CampaignPaths
    from app.runtime.stores import StoreBundle

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    base = NPCProfile(name="码头常客")
    fact_hit = NPCProfile(name="柴行简")
    unrelated = NPCProfile(name="无关路人")
    event_hit = NPCProfile(name="林澈")
    with stores.npc_profile() as store:
        for profile in (base, fact_hit, unrelated, event_hit):
            store.upsert(profile)
    with stores.director_event() as event_store:
        event_store.save(DirectorEvent(
            role="arc",
            title="商路封锁",
            subject="河务署",
            intent="控制渡河通道",
            method="封锁旧桥",
            macro_intent="改变商路格局",
            deadline_clock=WorldClock().advance_phase(3),
            created_turn=1,
            updated_turn=1,
            actors=[
                DirectorActorRef(
                    name="林澈",
                    ref=DirectorResourceRef(
                        kind="npc",
                        resource_id=event_hit.npc_id,
                    ),
                ),
            ],
        ))

    captured = {}
    streamed = []

    class _FakeComposition:
        scene_reasoning = "scene reasoning"
        scene_text = "场景正文。"
        next_action_directions = []

        def to_outline(self):
            return SimpleNamespace()

        def to_scene_result(self):
            return SimpleNamespace()

    class _FakeComposer:
        def __init__(self, gateway):
            self.gateway = gateway

        def compose(self, context, **kwargs):
            captured["context"] = context
            return _FakeComposition()

    monkeypatch.setattr(generation_nodes, "NarrativeComposerAgent", _FakeComposer)
    monkeypatch.setattr(generation_nodes, "build_entity_retrieval_anchors", lambda _outline: [])
    monkeypatch.setattr(generation_nodes, "get_stream_writer", lambda: streamed.append)
    monkeypatch.setattr(
        generation_nodes,
        "select_profiles_by_context_facts",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("composer must reuse stored selection, not rescan")
        ),
    )

    state = {
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: object(),
        ),
        "user_input": "观察码头。",
        "core_memory": CoreMemory(items=[]),
        "current_state": CurrentState(
            scene=SceneState(scene_id="scene_1", location="码头", participants=[]),
            player=PlayerState(name="萧云痕"),
            resources=ResourcesState(inventory=[]),
        ),
        "turn_resolution": TurnResolutionResult(),
        "context_facts": ContextFactSet(
            turn_focus="围绕昨夜商路的事展开。",
            facts=[ContextFact(
                fact_id="f1",
                fact="柴行简的商队昨夜通过了旧桥。",
            )],
        ),
        "trace_id": "trace-gen",
        "short_term_memory": SimpleNamespace(recent_scenes=[]),
        "action_npc_profiles": [base],
        "context_fact_npc_profiles": [fact_hit],
    }

    result = generation_nodes.compose_narrative_node(state)

    names = {
        profile.name for profile in captured["context"].npc_profiles
    }
    assert names == {"码头常客", "柴行简"}
    assert "无关路人" not in names
    assert "林澈" not in names
    assert result["context"] is captured["context"]


def test_extract_context_facts_preserves_facts_when_npc_selection_fails(
    tmp_path,
    monkeypatch,
):
    from app.domain.context_facts.schemas import ContextFact, ContextFactSet
    from app.domain.core_memory.schemas import CoreMemory
    from app.domain.current_state import (
        CurrentState,
        PlayerState,
        ResourcesState,
        SceneState,
    )
    from app.runtime.paths import CampaignPaths
    from app.runtime.stores import StoreBundle

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))

    class _FakeContextFactsService:
        def __init__(self, gateway, history_store, continuity_store, setting_store):
            pass

        def build(self, input, trace_id):
            return SimpleNamespace(
                context_facts=ContextFactSet(
                    turn_focus="围绕昨夜码头的事。",
                    facts=[ContextFact(
                        fact_id="f1",
                        fact="顾晚棠藏在客栈上房。",
                    )],
                ),
                search_plan=None,
                history_evidence=None,
                continuity=None,
                settings=SimpleNamespace(settings=[]),
            )

    def _boom(**kwargs):
        raise RuntimeError("profile db unavailable")

    monkeypatch.setattr(
        generation_nodes, "ContextFactsService", _FakeContextFactsService
    )
    monkeypatch.setattr(
        generation_nodes, "select_profiles_by_context_facts", _boom
    )

    state = {
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: object(),
        ),
        "user_input": "去客栈找顾晚棠。",
        "core_memory": CoreMemory(items=[]),
        "current_state": CurrentState(
            scene=SceneState(scene_id="scene_1", location="客栈", participants=[]),
            player=PlayerState(name="萧云痕"),
            resources=ResourcesState(inventory=[]),
        ),
        "scene_summaries": [],
        "matched_anchors": [],
        "action_npc_director_views": [],
        "next_turn_index": 2,
        "trace_id": "trace-extract",
    }

    result = generation_nodes.extract_context_facts_node(state)

    assert result["context_extract_error"] is None
    assert result["context_facts"].facts[0].fact == "顾晚棠藏在客栈上房。"
    assert result["context_fact_npc_profiles"] == []


def test_director_agent_stage_node_keeps_event_actors_when_action_saturates(
    tmp_path,
    monkeypatch,
):
    from app.domain.context_facts.schemas import ContextFactSet
    from app.domain.core_memory.schemas import CoreMemory
    from app.domain.current_state import (
        CurrentState,
        PlayerState,
        ResourcesState,
        SceneState,
        WorldClock,
    )
    from app.domain.director import DirectorResourceRef
    from app.domain.director.schemas import DirectorDecisionBatch
    from app.domain.director_event.schemas import DirectorActorRef, DirectorEvent
    from app.domain.npc_profile.schemas import NPCProfile
    from app.domain.turn_resolution.schemas import TurnResolutionResult
    from app.runtime.paths import CampaignPaths
    from app.runtime.stores import StoreBundle

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    action_profiles = [NPCProfile(name=f"在场人物{i}") for i in range(20)]
    event_hit = NPCProfile(name="事件演员")
    with stores.npc_profile() as store:
        for profile in (*action_profiles, event_hit):
            store.upsert(profile)
    with stores.director_event() as event_store:
        event_store.save(DirectorEvent(
            role="arc",
            title="商路封锁",
            subject="河务署",
            intent="控制渡河通道",
            method="封锁旧桥",
            macro_intent="改变商路格局",
            deadline_clock=WorldClock().advance_phase(3),
            created_turn=1,
            updated_turn=1,
            actors=[
                DirectorActorRef(
                    name="事件演员",
                    ref=DirectorResourceRef(
                        kind="npc",
                        resource_id=event_hit.npc_id,
                    ),
                ),
            ],
        ))

    captured = {}

    class _FakeExecutor:
        def __init__(self, gateway=None, stores=None, **kwargs):
            self._kwargs = kwargs

        def run(self):
            captured["npc_profiles"] = self._kwargs.get("npc_profiles")
            return (
                SimpleNamespace(),
                DirectorDecisionBatch(reasoning="本轮无需导演介入。", requests=[]),
            )

    monkeypatch.setattr(generation_nodes, "DirectorGenerationExecutor", _FakeExecutor)

    state = {
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: object(),
        ),
        "user_input": "观察码头。",
        "core_memory": CoreMemory(items=[]),
        "current_state": CurrentState(
            scene=SceneState(scene_id="scene_1", location="码头", participants=[]),
            player=PlayerState(name="萧云痕"),
            resources=ResourcesState(inventory=[]),
        ),
        "context_facts": ContextFactSet(turn_focus="围绕商路。", facts=[]),
        "recent_summaries": [],
        "turn_index": 3,
        "trace_id": "trace-gen",
        "turn_resolution": TurnResolutionResult(),
        "touched_event_candidates": [],
        "action_npc_profiles": action_profiles,
        "context_fact_npc_profiles": [],
    }

    generation_nodes.director_agent_stage_node(state)

    observed = {profile.npc_id for profile in captured["npc_profiles"]}
    assert event_hit.npc_id in observed


def test_composer_npc_profiles_respect_disclosure_levels(
    tmp_path,
    monkeypatch,
):
    from app.domain.context_facts.schemas import ContextFactSet
    from app.domain.core_memory.schemas import CoreMemory
    from app.domain.current_state import (
        CurrentState,
        PlayerState,
        ResourcesState,
        SceneState,
    )
    from app.domain.npc_profile.schemas import NPCProfile
    from app.domain.turn_resolution.schemas import TurnResolutionResult
    from app.runtime.paths import CampaignPaths
    from app.runtime.stores import StoreBundle

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    remote_npc = NPCProfile(
        name="林澈",
        role="社区医生",
        appearance="常穿深色风衣。",
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        disclosure_level="remote",
    )
    intimate_npc = NPCProfile(
        name="顾晚棠",
        role="阁主",
        hidden_secret="暗中资助商会。",
        disclosure_level="intimate",
    )
    with stores.npc_profile() as store:
        store.upsert(remote_npc)
        store.upsert(intimate_npc)

    captured = {}
    streamed = []

    class _FakeComposition:
        scene_reasoning = "r"
        scene_text = "t"
        next_action_directions = []

        def to_outline(self):
            return SimpleNamespace()

        def to_scene_result(self):
            return SimpleNamespace()

    class _FakeComposer:
        def __init__(self, gateway):
            self.gateway = gateway

        def compose(self, context, **kwargs):
            captured["context"] = context
            return _FakeComposition()

    monkeypatch.setattr(generation_nodes, "NarrativeComposerAgent", _FakeComposer)
    monkeypatch.setattr(generation_nodes, "build_entity_retrieval_anchors", lambda _o: [])
    monkeypatch.setattr(generation_nodes, "get_stream_writer", lambda: streamed.append)

    state = {
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: object(),
        ),
        "user_input": "观察码头。",
        "core_memory": CoreMemory(items=[]),
        "current_state": CurrentState(
            scene=SceneState(scene_id="scene_1", location="码头", participants=[]),
            player=PlayerState(name="萧云痕"),
            resources=ResourcesState(inventory=[]),
        ),
        "turn_resolution": TurnResolutionResult(),
        "context_facts": ContextFactSet(turn_focus="围绕码头。", facts=[]),
        "trace_id": "trace-gen",
        "short_term_memory": SimpleNamespace(recent_scenes=[]),
        "action_npc_profiles": [remote_npc, intimate_npc],
        "context_fact_npc_profiles": [],
    }

    generation_nodes.compose_narrative_node(state)

    by_name = {
        profile.name: profile
        for profile in captured["context"].npc_profiles
    }
    assert by_name["林澈"].hidden_secret is None
    assert by_name["林澈"].core_motivation is None
    assert by_name["顾晚棠"].hidden_secret == "暗中资助商会。"


def test_context_facts_npc_profiles_respect_disclosure_levels(
    tmp_path,
    monkeypatch,
):
    from app.domain.context_facts.schemas import ContextFactSet
    from app.domain.core_memory.schemas import CoreMemory
    from app.domain.current_state import (
        CurrentState,
        PlayerState,
        ResourcesState,
        SceneState,
    )
    from app.domain.npc_profile.schemas import NPCProfile
    from app.runtime.paths import CampaignPaths
    from app.runtime.stores import StoreBundle

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    remote_npc = NPCProfile(
        name="林澈",
        role="社区医生",
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        disclosure_level="remote",
    )
    with stores.npc_profile() as store:
        store.upsert(remote_npc)

    captured = {}

    class _FakeContextFactsService:
        def __init__(self, gateway, history_store, continuity_store, setting_store):
            pass

        def build(self, input, trace_id):
            captured["npc_profiles"] = input.npc_profiles
            return SimpleNamespace(
                context_facts=ContextFactSet(
                    turn_focus="围绕码头。",
                    facts=[],
                ),
                search_plan=None,
                history_evidence=None,
                continuity=None,
                settings=SimpleNamespace(settings=[]),
            )

    monkeypatch.setattr(
        generation_nodes, "ContextFactsService", _FakeContextFactsService
    )
    monkeypatch.setattr(
        generation_nodes,
        "select_profiles_by_context_facts",
        lambda **_kwargs: [],
    )

    state = {
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: object(),
        ),
        "user_input": "观察码头。",
        "core_memory": CoreMemory(items=[]),
        "current_state": CurrentState(
            scene=SceneState(scene_id="scene_1", location="码头", participants=[]),
            player=PlayerState(name="萧云痕"),
            resources=ResourcesState(inventory=[]),
        ),
        "scene_summaries": [],
        "matched_anchors": [],
        "next_turn_index": 2,
        "trace_id": "trace-extract",
        "action_npc_profiles": [remote_npc],
    }

    generation_nodes.extract_context_facts_node(state)

    view = captured["npc_profiles"][0]
    assert view.name == "林澈"
    assert view.hidden_secret is None
    assert view.core_motivation is None


def test_composer_receives_engaged_events_only_via_intrude(
    tmp_path,
    monkeypatch,
):
    from app.domain.context_facts.schemas import ContextFactSet
    from app.domain.core_memory.schemas import CoreMemory
    from app.domain.current_state import (
        CurrentState,
        PlayerState,
        ResourcesState,
        SceneState,
        WorldClock,
    )
    from app.domain.director.schemas import (
        DirectorStageMaterial,
        DirectorStageResult,
    )
    from app.domain.director_event.schemas import DirectorEvent
    from app.domain.npc_profile.schemas import NPCProfile
    from app.domain.turn_resolution.schemas import TurnResolutionResult
    from app.runtime.paths import CampaignPaths
    from app.runtime.stores import StoreBundle

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile() as store:
        store.upsert(NPCProfile(name="码头常客"))
    engaged = DirectorEvent(
        role="arc",
        title="商路封锁",
        subject="河务署",
        intent="控制渡河通道",
        method="封锁旧桥",
        macro_intent="改变商路格局",
        deadline_clock=WorldClock().advance_phase(3),
        created_turn=1,
        updated_turn=1,
        stage="engaged",
    )
    just_engaged_hidden = DirectorEvent(
        role="ambient",
        title="无灯船",
        subject="猎户",
        intent="查明异动",
        method="搜山",
        macro_intent="生存不择手段。",
        deadline_clock=WorldClock().advance_phase(2),
        created_turn=1,
        updated_turn=1,
        stage="hidden",
    )
    unrelated_hidden = DirectorEvent(
        role="ambient",
        title="旧巷传闻",
        subject="镇民",
        intent="散布流言",
        method="茶楼闲谈",
        macro_intent="转移视线。",
        deadline_clock=WorldClock().advance_phase(2),
        created_turn=1,
        updated_turn=1,
        stage="hidden",
    )
    with stores.director_event() as store:
        store.save(engaged)
        store.save(just_engaged_hidden)
        store.save(unrelated_hidden)

    captured = {}
    streamed = []

    class _FakeComposition:
        scene_reasoning = "r"
        scene_text = "t"
        next_action_directions = []

        def to_outline(self):
            return SimpleNamespace()

        def to_scene_result(self):
            return SimpleNamespace()

    class _FakeComposer:
        def __init__(self, gateway):
            self.gateway = gateway

        def compose(self, context, **kwargs):
            captured["context"] = context
            return _FakeComposition()

    monkeypatch.setattr(generation_nodes, "NarrativeComposerAgent", _FakeComposer)
    monkeypatch.setattr(generation_nodes, "build_entity_retrieval_anchors", lambda _o: [])
    monkeypatch.setattr(generation_nodes, "get_stream_writer", lambda: streamed.append)

    def _base_state():
        return {
            "runtime_services": SimpleNamespace(
                stores=stores,
                gateway=lambda **kwargs: object(),
            ),
            "user_input": "观察码头。",
            "core_memory": CoreMemory(items=[]),
            "current_state": CurrentState(
                scene=SceneState(scene_id="scene_1", location="码头", participants=[]),
                player=PlayerState(name="萧云痕"),
                resources=ResourcesState(inventory=[]),
            ),
            "turn_resolution": TurnResolutionResult(),
            "context_facts": ContextFactSet(turn_focus="围绕码头。", facts=[]),
            "trace_id": "trace-gen",
            "short_term_memory": SimpleNamespace(recent_scenes=[]),
            "action_npc_profiles": [],
            "context_fact_npc_profiles": [],
            "touched_event_candidates": [just_engaged_hidden.event_id],
        }

    # 无导演调度：玩家本轮直接卷入的事件也不可见
    state = _base_state()
    generation_nodes.compose_narrative_node(state)
    auto_ids = {
        event.event_id for event in captured["context"].engaged_events
    }
    assert auto_ids == set()

    # 导演调度一个已有 engaged 事件后，只有被调度的事件可见
    state = _base_state()
    state["director_stage"] = DirectorStageResult(
        reasoning="调度商路封锁。",
        materials=[
            DirectorStageMaterial(
                kind="engaged_event",
                resource_id=engaged.event_id,
                guidance="商路封锁作为本回合的实质干涉。",
                payload={"event_id": engaged.event_id, "stage": "engaged"},
            )
        ],
    )
    generation_nodes.compose_narrative_node(state)

    injected_ids = {
        event.event_id for event in captured["context"].engaged_events
    }
    assert injected_ids == {engaged.event_id}
    assert just_engaged_hidden.event_id not in injected_ids
    assert unrelated_hidden.event_id not in injected_ids

    from app.domain.director_event.views import project_director_events

    projected = json.loads(
        project_director_events(
            captured["context"].engaged_events,
            effective_stage="engaged",
        )
    )
    engaged_view = next(
        item
        for item in projected
        if item["event_id"] == engaged.event_id
    )
    assert engaged_view["stage"] == "engaged"
    assert engaged_view["macro_intent"] == "改变商路格局"
    assert "hooks" not in engaged_view


def test_composer_hides_newly_touched_events_without_intrude(
    tmp_path,
    monkeypatch,
):
    from app.domain.context_facts.schemas import ContextFactSet
    from app.domain.core_memory.schemas import CoreMemory
    from app.domain.current_state import (
        CurrentState,
        PlayerState,
        ResourcesState,
        SceneState,
        WorldClock,
    )
    from app.domain.director_event.schemas import DirectorEvent
    from app.domain.npc_profile.schemas import NPCProfile
    from app.domain.turn_resolution.schemas import TurnResolutionResult
    from app.runtime.paths import CampaignPaths
    from app.runtime.stores import StoreBundle

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile() as store:
        store.upsert(NPCProfile(name="码头常客"))

    def _event(event_id: str, title: str) -> DirectorEvent:
        return DirectorEvent(
            role="ambient",
            title=title,
            subject="镇民",
            intent="异动",
            method="查探",
            macro_intent="事件背景。",
            deadline_clock=WorldClock().advance_phase(2),
            created_turn=1,
            updated_turn=1,
            stage="hidden",
        )

    first = _event("director_event_touch_a", "夜路响动")
    second = _event("director_event_touch_b", "河面浮灯")
    unrelated = _event("director_event_touch_unrelated", "旧巷传闻")
    with stores.director_event() as store:
        store.save(first)
        store.save(second)
        store.save(unrelated)

    captured = {}
    streamed = []

    class _FakeComposition:
        scene_reasoning = "r"
        scene_text = "t"
        next_action_directions = []

        def to_outline(self):
            return SimpleNamespace()

        def to_scene_result(self):
            return SimpleNamespace()

    class _FakeComposer:
        def __init__(self, gateway):
            self.gateway = gateway

        def compose(self, context, **kwargs):
            captured["context"] = context
            return _FakeComposition()

    monkeypatch.setattr(generation_nodes, "NarrativeComposerAgent", _FakeComposer)
    monkeypatch.setattr(generation_nodes, "build_entity_retrieval_anchors", lambda _o: [])
    monkeypatch.setattr(generation_nodes, "get_stream_writer", lambda: streamed.append)

    state = {
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: object(),
        ),
        "user_input": "先查夜路响动，再去河面看浮灯。",
        "core_memory": CoreMemory(items=[]),
        "current_state": CurrentState(
            scene=SceneState(scene_id="scene_1", location="码头", participants=[]),
            player=PlayerState(name="萧云痕"),
            resources=ResourcesState(inventory=[]),
        ),
        "turn_resolution": TurnResolutionResult(),
        "context_facts": ContextFactSet(turn_focus="围绕码头。", facts=[]),
        "trace_id": "trace-gen",
        "short_term_memory": SimpleNamespace(recent_scenes=[]),
        "action_npc_profiles": [],
        "context_fact_npc_profiles": [],
        "touched_event_candidates": [first.event_id, second.event_id],
        "director_stage": None,
    }

    generation_nodes.compose_narrative_node(state)

    injected_ids = {
        event.event_id for event in captured["context"].engaged_events
    }
    assert injected_ids == set()
    assert unrelated.event_id not in injected_ids
