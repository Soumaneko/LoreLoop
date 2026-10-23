from types import SimpleNamespace

from app.domain.action.schemas import (
    ActionParseResult,
    ActionRetrievalTopic,
    AtomicAction,
)
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState
from app.domain.roll.schemas import RollResultBatch
from app.domain.roll_modifier.schemas import RollModifierBatch
from app.graph.confirmation_cache import ConfirmationCache
from app.graph.generation_helpers import (
    extract_generation_prefix,
    hydrate_generation_prefix,
    reset_generation_suffix,
)
from app.graph.generation_nodes import resolve_rolls_node
from app.runtime.session import (
    GenerationPrefixCache,
    GenerationPrefixSnapshot,
)


def _make_snapshot(has_fallback=False, needs_roll=False):
    parse_result = ActionParseResult(
        atomic_actions=[
            AtomicAction(
                action_id="action_1",
                sequence=1,
                actor=["萧欢"],
                action_type="attack" if needs_roll else "inspect_object",
                raw_text="攻击敌人" if needs_roll else "测试动作",
                verb="攻击" if needs_roll else "测试",
                likely_needs_roll=needs_roll,
            )
        ],
        retrieval_topics=[ActionRetrievalTopic(text="测试话题")],
    )
    context_facts = ContextFactSet(turn_focus="测试焦点", facts=[])
    return GenerationPrefixSnapshot(
        action_parse_result=parse_result,
        raw_action_parse_result=parse_result,
        action_self_check_result=None,
        context_facts=context_facts,
        context_search_plan=None,
        context_history_evidence=None,
        continuity_selection=None,
        context_fact_npc_profiles=[],
        matched_anchors=[],
        action_npc_profiles=[],
        action_npc_director_views=[],
        touched_event_candidates=[],
        modifier_batch=RollModifierBatch(),
        has_fallback=has_fallback,
    )


def test_generation_prefix_cache_lifecycle_and_epoch():
    cache = GenerationPrefixCache(max_size=2)
    snap1 = _make_snapshot()
    snap_fallback = _make_snapshot(has_fallback=True)

    # Fallback snapshots are rejected from cache admission
    cache.set(0, "失败输入", snap_fallback)
    assert cache.get(0, "失败输入") is None

    # Normal snapshots are stored and retrieved
    cache.set(0, "正常输入", snap1)
    retrieved = cache.get(0, "正常输入")
    assert retrieved is not None
    assert len(retrieved.action_parse_result.atomic_actions) == 1

    # Epoch mismatch returns None
    assert cache.get(1, "正常输入") is None

    # Deepcopy check: mutating retrieved does not affect cache
    retrieved.action_npc_profiles.append("polluted")
    fresh = cache.get(0, "正常输入")
    assert len(fresh.action_npc_profiles) == 0

    # Clear resets
    cache.clear()
    assert cache.get(0, "正常输入") is None


def test_extract_and_hydrate_prefix_helpers():
    snap = _make_snapshot()
    state = {
        "user_input": "测试动作",
        "action_parse_result": snap.action_parse_result,
        "context_facts": snap.context_facts,
        "modifier_batch": snap.modifier_batch,
        "events": [],
        "roll_results": None,
        "action_judgments": [],
    }

    extracted = extract_generation_prefix(state)
    assert extracted.has_fallback is False
    assert extracted.action_parse_result == snap.action_parse_result

    # Reset suffix clears downstream artifacts
    reset_generation_suffix(state)
    assert "roll_results" not in state
    assert "action_judgments" not in state

    # Hydrate restores prefix fields
    target_state = {}
    hydrate_generation_prefix(target_state, extracted)
    assert target_state["action_parse_result"] == snap.action_parse_result
    assert target_state["context_facts"] == snap.context_facts






def _make_state() -> CurrentState:
    from app.domain.current_state.schemas import PlayerState, ResourcesState, SceneState
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="驻地"),
        player=PlayerState(name="萧欢"),
        resources=ResourcesState(),
    )


def test_reroll_turn_stream_uses_reroll_graph(monkeypatch, configure_runtime_services):
    from app.domain.narrative_composer.schemas import NarrativeComposerContext
    from app.domain.plot_outline.schemas import PlotOutlineResult
    from app.domain.scene_writer.schemas import SceneWriterResult
    from app.domain.short_term_memory import ShortTermMemory
    from app.graph import generation_graph
    from app.runtime.session import PendingTurn, get_campaign_runtime

    runtime = get_campaign_runtime()
    runtime.stores.core_memory.save(CoreMemory(items=[]))
    turn_id = "turn_reroll_test"
    group_id = "group_reroll_test"
    state_before = _make_state()
    snap = _make_snapshot()

    pending = PendingTurn(
        turn_id=turn_id,
        group_id=group_id,
        version_index=1,
        user_input="测试动作",
        difficulty="困难",
        max_chars=1500,
        experience_mode="player",
        state_before=state_before,
        context=NarrativeComposerContext(
            user_input="测试动作",
            current_state=state_before,
            action_judgments=[],
            core_memory=CoreMemory(items=[]),
            context_facts=snap.context_facts,
        ),
        outline=PlotOutlineResult(scene_stopping_point="停顿点"),
        scene_result=SceneWriterResult(scene_text="旧正文"),
        turn_index=1,
        short_term_memory=ShortTermMemory(),
        prefix_snapshot=snap,
    )
    runtime.pending_turns.add(pending)

    captured = {}

    class _FakeStateStore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load_or_default(self):
            return state_before

    configure_runtime_services(current_state=_FakeStateStore)

    def _fake_stream(state, graph, **kwargs):
        captured["state"] = state
        captured["graph"] = graph
        yield {"step": "scene_written", "data": {"turn_id": "turn_v2"}}

    monkeypatch.setattr(generation_graph, "_stream_generation_graph", _fake_stream)

    try:
        events = list(generation_graph.reroll_turn_stream(turn_id))
        assert events[0]["step"] == "reroll_started"
        assert events[-1]["step"] == "scene_written"
        # Assert prefix was hydrated and suffix was reset
        assert captured["state"]["action_parse_result"] == snap.action_parse_result
        assert captured["state"]["context_facts"] == snap.context_facts
        assert "action_judgments" not in captured["state"]
    finally:
        runtime.pending_turns.remove(turn_id)


def test_generate_turn_stream_uses_prefix_cache_and_invalidates(campaign_runtime, monkeypatch):
    from app.domain.core_memory import CoreMemory
    from app.domain.short_term_memory import ShortTermMemory
    from app.graph import generation_graph
    from app.runtime.session import get_campaign_runtime

    runtime = get_campaign_runtime()
    user_input = "缓存命中动作"
    snap = _make_snapshot()

    # Pre-populate prefix cache
    runtime.generation_prefix_cache.set(
        runtime.generation_cache_epoch,
        user_input,
        snap,
    )

    captured = {}

    def _fake_prepare(user_input, difficulty, max_chars):
        return {
            "events": [],
            "user_input": user_input,
            "difficulty": difficulty,
            "max_chars": max_chars,
            "core_memory": CoreMemory(items=[]),
            "current_state": _make_state(),
            "short_term_memory": ShortTermMemory(),
            "turn_index": 1,
            "next_turn_index": 1,
            "scene_summaries": [],
            "recent_summaries": [],
            "matched_anchors": [],
                "action_npc_profiles": [],
                "action_npc_director_views": [],
                "runtime_services": SimpleNamespace(
                    stores=SimpleNamespace(close=lambda: None)
                ),
            }

    monkeypatch.setattr(generation_graph, "prepare_generation_state", _fake_prepare)

    def _fake_stream(state, graph, **kwargs):
        captured["state"] = state
        captured["has_action_parse"] = "action_parse_result" in state
        yield {"step": "scene_written", "data": {"turn_id": "turn_gen"}}

    monkeypatch.setattr(generation_graph, "_stream_generation_graph", _fake_stream)

    # 1. First run with cached input: should hydrate action_parse_result
    list(generation_graph._generate_turn_stream(user_input, "困难", 1500))
    assert captured["has_action_parse"] is True
    assert captured["state"]["action_parse_result"] == snap.action_parse_result

    # 2. Canonical mutation: invalidate cache
    runtime.invalidate_generation_cache()

    # 3. Second run with same input: cache missed, action_parse_result not pre-hydrated
    list(generation_graph._generate_turn_stream(user_input, "困难", 1500))
    assert captured["has_action_parse"] is False


def test_finalize_pending_turn_populates_snapshot_and_prefix_cache(campaign_runtime):
    from app.domain.plot_outline.schemas import PlotOutlineResult
    from app.domain.scene_writer.schemas import SceneWriterResult
    from app.domain.short_term_memory import ShortTermMemory
    from app.graph.generation_helpers import finalize_pending_turn
    from app.runtime.session import get_campaign_runtime

    runtime = get_campaign_runtime()
    snap = _make_snapshot()
    user_input = "测试真实finalize"

    state = {
        "user_input": user_input,
        "difficulty": "困难",
        "max_chars": 1500,
        "experience_mode": "player",
        "trace_id": "test_trace_123",
        "current_state": _make_state(),
        "next_turn_index": 1,
        "short_term_memory": ShortTermMemory(),
        "action_parse_result": snap.action_parse_result,
        "context_facts": snap.context_facts,
        "context": None,
        "outline": PlotOutlineResult(scene_stopping_point="停顿点"),
        "scene_result": SceneWriterResult(scene_text="正文"),
        "roll_results": RollResultBatch(dice_formula="1d100", dice_sides=100, total_rolls=0, results=[]),
        "action_judgments": [],
        "player_resolution": None,
        "events": [],
    }

    # Verify finalize_pending_turn binds prefix_snapshot and writes to prefix cache
    finalized = finalize_pending_turn(state)
    turn_id = finalized["turn_id"]
    pending = runtime.pending_turns.get(turn_id)

    try:
        assert pending is not None
        assert pending.prefix_snapshot is not None
        assert pending.prefix_snapshot.action_parse_result == snap.action_parse_result

        # Assert prefix cache is populated
        cached = runtime.generation_prefix_cache.get(
            runtime.generation_cache_epoch,
            user_input,
        )
        assert cached is not None
        assert cached.action_parse_result == snap.action_parse_result
    finally:
        runtime.pending_turns.remove(turn_id)
