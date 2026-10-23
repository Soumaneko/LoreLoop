from app.domain.current_state import make_default_state
from app.domain.core_memory import CoreMemory
from app.domain.director import DirectorStageResult
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.plot_outline.schemas import PlotOutlineResult
from types import SimpleNamespace

import json

import pytest
from app.core.diagnostics import NullDiagnosticLogger
from app.core.llm.errors import LLMOutputValidationError

from langgraph.graph import END, START, StateGraph

from app.graph import generation_graph, generation_nodes
from app.domain.narrative_canon_query.schemas import NarrativeCanonQuerySet
from app.domain.narrative_rewrite.schemas import RewriteStrategyDecision
from app.graph.generation_events import GenerationEventPresenter
from app.graph.generation_state import GenerationState
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory
from app.runtime.session import PendingTurn, get_campaign_runtime
from app.runtime.settings import save_experience_mode


def test_generation_captures_experience_mode_snapshot_at_generation(campaign_runtime, monkeypatch):
    class _FakeGraph:
        def stream(self, _state, stream_mode):
            yield "updates", {}

    captured = {}

    def _prepare(user_input, difficulty, max_chars):
        return {
            "events": [],
            "user_input": user_input,
            "difficulty": difficulty,
            "max_chars": max_chars,
            "runtime_services": SimpleNamespace(
                stores=SimpleNamespace(close=lambda: None)
            ),
        }

    def _finalize(state, **_kwargs):
        captured["state"] = state
        return {**state, "events": []}

    monkeypatch.setattr(
        generation_graph, "prepare_generation_state", _prepare
    )
    monkeypatch.setattr(
        generation_graph, "build_generation_graph", lambda **_kwargs: _FakeGraph()
    )
    monkeypatch.setattr(generation_graph, "finalize_pending_turn", _finalize)

    save_experience_mode({"experience_mode": "creator"})
    list(generation_graph.generate_turn_stream("观察"))

    assert captured["state"]["experience_mode"] == "creator"
    assert captured["state"]["difficulty"] == "困难"

    save_experience_mode({"experience_mode": "player"})
    list(generation_graph.generate_turn_stream("观察"))

    assert captured["state"]["experience_mode"] == "player"






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
            "runtime_services": SimpleNamespace(
                stores=SimpleNamespace(close=lambda: None)
            ),
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








def test_reroll_creates_next_version_in_same_group(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "turn_v1"
    group_id = "group_1"
    state_before = make_default_state()
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(PendingTurn(
        turn_id=turn_id,
        group_id=group_id,
        version_index=1,
        user_input="观察门外",
        difficulty="普通",
        max_chars=900,
        experience_mode="creator",
        state_before=state_before,
        context=NarrativeComposerContext(user_input="观察门外", current_state=state_before, core_memory=CoreMemory(items=[])),
        outline=PlotOutlineResult(scene_stopping_point="门外传来脚步声。"),
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
    state_before = make_default_state()
    context = NarrativeComposerContext(user_input="观察门外", current_state=state_before, core_memory=CoreMemory(items=[]))
    director_stage = DirectorStageResult(reasoning="门外来人。")
    short_term = ShortTermMemory()
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(PendingTurn(
        turn_id=turn_id,
        group_id=group_id,
        version_index=1,
        user_input="观察门外",
        difficulty="普通",
        max_chars=900,
        experience_mode="creator",
        state_before=state_before,
        context=context,
        outline=PlotOutlineResult(scene_stopping_point="门外传来脚步声。"),
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
    assert state["action_judgments"] is context.action_judgments
    assert state["player_resolution"] is player_resolution
    assert state["current_state"] is state_before
    assert state["short_term_memory"] is short_term
    assert state["director_stage"] is director_stage
    assert events[-1]["data"]["turn_id"] == "turn_v2"




def test_feedback_rewrite_cancellation_after_first_event_closes_uow(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "turn_v1"
    state_before = make_default_state()
    context = NarrativeComposerContext(user_input="观察门外", current_state=state_before, core_memory=CoreMemory(items=[]))
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(PendingTurn(
        turn_id=turn_id,
        group_id="group_1",
        version_index=1,
        user_input="观察门外",
        difficulty="普通",
        max_chars=900,
        experience_mode="creator",
        state_before=state_before,
        context=context,
        outline=PlotOutlineResult(scene_stopping_point="门外传来脚步声。"),
        scene_result=SceneWriterResult(scene_text="版本一"),
        turn_index=3,
        short_term_memory=ShortTermMemory(),
    ))

    class _StateStore:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def load_or_default(self): return state_before

    configure_runtime_services(
        current_state=_StateStore,
        gateway_factory=lambda: SimpleNamespace(
            set_trace_context=lambda **_kwargs: None
        ),
    )

    try:
        gen = generation_graph.feedback_rewrite_stream(
            turn_id,
            source_scene_text="版本一",
            user_feedback="收紧",
        )
        first = next(gen)
        assert first["step"] == "feedback_rewrite_started"
        # Simulate SSE disconnect: abandon the generator without consuming rest.
        gen.close()
        # The operation UoW must be unregistered even though only the first
        # event was emitted.
        assert len(runtime._active_uows) == 0
    finally:
        runtime.pending_turns.remove(turn_id)














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
    from app.runtime.paths import CampaignPaths
    from app.runtime.uow import CampaignUnitOfWork

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
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
    with stores.npc_profile as store:
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
        "action_judgments": [],
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


def test_composer_receives_engaged_event_actor_profiles(
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
    from app.domain.director import DirectorStageMaterial, DirectorStageResult
    from app.domain.director_event.schemas import (
        DirectorActorRef,
        DirectorEvent,
        DirectorResourceRef,
    )
    from app.domain.director_event.schemas import WorldClock
    from app.domain.npc_profile.schemas import NPCProfile
    from app.runtime.paths import CampaignPaths
    from app.runtime.uow import CampaignUnitOfWork

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    samuel = NPCProfile(
        name="萨缪尔·灰潮",
        role="走私联络人",
        appearance="深灰色旧风衣，袖口褪色铜徽章。",
        last_scene_summary="在马车旁取走油布包裹。",
        last_scene_turn=2,
    )
    trevor = NPCProfile(
        name="特雷弗·石巷",
        role="跑腿小贼",
        appearance="瘦高，袖口有墨渍。",
        last_scene_summary="在客栈门口被催促离场。",
        last_scene_turn=6,
    )
    with stores.npc_profile as store:
        store.upsert(samuel)
        store.upsert(trevor)
    event = DirectorEvent(
        title="走私暗流",
        subject="走私网络退潮",
        intent="把货物送入市场。",
        method="利用特许印签。",
        macro_intent="扩大地下流通。",
        deadline_clock=WorldClock(),
        created_turn=1,
        updated_turn=1,
        stage="engaged",
        actors=[
            DirectorActorRef(
                name=samuel.name,
                ref=DirectorResourceRef(
                    kind="npc", resource_id=samuel.npc_id
                ),
            )
        ],
    )
    with stores.director_event as event_store:
        event_store.save(event)

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
        "user_input": "观察门口。",
        "core_memory": CoreMemory(items=[]),
        "current_state": CurrentState(
            scene=SceneState(scene_id="scene_1", location="码头", participants=[]),
            player=PlayerState(name="阿尔文"),
            resources=ResourcesState(inventory=[]),
        ),
        "action_judgments": [],
        "context_facts": ContextFactSet(turn_focus="围绕码头。", facts=[]),
        "trace_id": "trace-gen",
        "short_term_memory": SimpleNamespace(recent_scenes=[]),
        "director_stage": DirectorStageResult(
            reasoning="安排萨缪尔介入。",
            materials=[
                DirectorStageMaterial(
                    kind="engaged_event",
                    resource_id=event.event_id,
                    guidance="以萨缪尔推动事件。",
                    payload=event.semantic_view(),
                ),
                DirectorStageMaterial(
                    kind="event",
                    resource_id="event_present",
                    guidance="通过传闻带出特雷弗。",
                    payload={
                        "event_id": "event_present",
                        "title": "传闻",
                        "stage": "hidden",
                        "hook": {},
                        "actors": [trevor.name],
                    },
                ),
            ],
        ),
    }

    generation_nodes.compose_narrative_node(state)

    by_name = {
        profile.name: profile
        for profile in captured["context"].npc_profiles
    }
    assert by_name["萨缪尔·灰潮"].last_scene_summary == "在马车旁取走油布包裹。"
    assert by_name["萨缪尔·灰潮"].last_scene_turn == 2
    assert by_name["特雷弗·石巷"].last_scene_summary == "在客栈门口被催促离场。"
    assert by_name["特雷弗·石巷"].last_scene_turn == 6












def test_feedback_rewrite_falls_back_to_strategy_unavailable(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "turn_v1"
    state_before = make_default_state()
    context = NarrativeComposerContext(user_input="观察门外", current_state=state_before, core_memory=CoreMemory(items=[]))
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(PendingTurn(
        turn_id=turn_id,
        group_id="group_1",
        version_index=1,
        user_input="观察门外",
        difficulty="普通",
        max_chars=900,
        experience_mode="creator",
        state_before=state_before,
        context=context,
        outline=PlotOutlineResult(scene_stopping_point="门外传来脚步声。"),
        scene_result=SceneWriterResult(scene_text="版本一"),
        turn_index=3,
        short_term_memory=ShortTermMemory(),
    ))

    class _StateStore:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def load_or_default(self): return state_before

    class _FailingSelector:
        def __init__(self, _gateway): pass
        def select(self, **_kwargs):
            raise LLMOutputValidationError("selector validation down")

    configure_runtime_services(
        current_state=_StateStore,
        gateway_factory=lambda: SimpleNamespace(
            set_trace_context=lambda **_kwargs: None
        ),
    )
    monkeypatch.setattr(generation_graph, "RewriteStrategySelector", _FailingSelector)
    monkeypatch.setattr(
        generation_graph,
        "_stream_generation_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fallback no_rewrite must not enter a generation graph")
        ),
    )

    try:
        events = list(generation_graph.feedback_rewrite_stream(
            turn_id,
            source_scene_text="版本一",
            user_feedback="帮我改一下",
        ))
    finally:
        runtime.pending_turns.remove(turn_id)

    assert [event["step"] for event in events] == [
        "feedback_rewrite_started",
        "feedback_rewrite_strategy_selected",
    ]
    assert events[-1]["data"]["strategy"] == "no_rewrite"
    assert events[-1]["data"]["reason_code"] == "strategy_unavailable"


def test_feedback_rewrite_rejects_source_scene_text_over_pending_max_chars(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "turn_v1"
    state_before = make_default_state()
    context = NarrativeComposerContext(user_input="观察门外", current_state=state_before, core_memory=CoreMemory(items=[]))
    runtime = get_campaign_runtime()
    runtime.pending_turns.add(PendingTurn(
        turn_id=turn_id,
        group_id="group_1",
        version_index=1,
        user_input="观察门外",
        difficulty="普通",
        max_chars=10,
        experience_mode="creator",
        state_before=state_before,
        context=context,
        outline=PlotOutlineResult(scene_stopping_point="门外传来脚步声。"),
        scene_result=SceneWriterResult(scene_text="版本一"),
        turn_index=3,
        short_term_memory=ShortTermMemory(),
    ))

    class _StateStore:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def load_or_default(self): return state_before

    configure_runtime_services(
        current_state=_StateStore,
        gateway_factory=lambda: SimpleNamespace(
            set_trace_context=lambda **_kwargs: None
        ),
    )

    try:
        with pytest.raises(ValueError, match="exceeds pending max_chars"):
            list(generation_graph.feedback_rewrite_stream(
                turn_id,
                source_scene_text="这个正文明显超过十个字",
                user_feedback="改一下",
            ))
    finally:
        runtime.pending_turns.remove(turn_id)


def _compose_test_state(**overrides):
    class _Store:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def list_active(self): return []
        def list_all(self): return []
    stores = SimpleNamespace(
        director_event=_Store(),
        npc_profile=_Store(),
    )
    state = {
        "runtime_services": SimpleNamespace(
            diagnostics=NullDiagnosticLogger(),
            stores=stores,
        ),
        "user_input": "观察。",
        "current_state": object(),
        "action_npc_profiles": [],
        "context_fact_npc_profiles": [],
        "touched_event_candidates": [],
        "director_stage": None,
        "context": object(),
        "core_memory": object(),
        "context_facts": object(),
        "short_term_memory": SimpleNamespace(recent_scenes=[]),
        "trace_id": "trace-compose",
        "next_turn_index": 1,
        "action_judgments": [],
    }
    state.update(overrides)
    return state


def _fake_output(state, composition):
    return {
        "narrative_composition": composition,
        "outline": SimpleNamespace(),
        "scene_result": composition.to_scene_result(),
        "beat_entity_anchors": [],
        "composer_npc_public_views": [],
    }
