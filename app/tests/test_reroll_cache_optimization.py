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


def test_resolve_rolls_node_reuses_modifier_batch():
    snap = _make_snapshot(needs_roll=True)
    state = {
        "action_parse_result": snap.action_parse_result,
        "modifier_batch": snap.modifier_batch,
        "difficulty": "简单",
    }
    result = resolve_rolls_node(state)
    assert "roll_results" in result
    assert "modifier_batch" in result
    assert any(ev["step"] == "rolls_done" for ev in result["events"])


def test_confirmation_cache_retains_preload_on_scene_text_change():
    cache_data = {
        "key": "旧正文",
        "existing_npc_profiles": [{"id": "npc_1"}],
        "director_npc_profile_ids": ["npc_1"],
        "scene_facts": {"facts": []},
        "updated_state": {"scene": {}},
        "turn_summary": {"summary": "旧摘要"},
        "state_saved": True,
    }
    cache = ConfirmationCache(cache_data)

    # When scene_text is the same, everything is preserved
    cache.begin("旧正文")
    assert cache.result("scene_facts") is not None
    assert cache.result("existing_npc_profiles") == [{"id": "npc_1"}]
    assert cache.completed("state_saved") is True

    # When scene_text changes, text results and markers are cleared, while preload (including director drafts from pending) is retained!
    cache.begin("新正文 - 玩家微调")
    assert cache.result("scene_facts") is None
    assert cache.result("updated_state") is None
    assert cache.result("turn_summary") is None
    assert cache.completed("state_saved") is False
    assert cache.result("director_npc_profile_ids") == ["npc_1"]
    assert cache.result("existing_npc_profiles") == [{"id": "npc_1"}]


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


def test_generate_turn_stream_uses_prefix_cache_and_invalidates(monkeypatch):
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


def test_finalize_pending_turn_populates_snapshot_and_prefix_cache():
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


def test_settings_update_invalidates_prefix_cache():
    from app.runtime.session import get_campaign_runtime
    from app.runtime.settings import save_difficulty, save_experience_mode

    runtime = get_campaign_runtime()
    snap = _make_snapshot()
    user_input = "设置变更失效测试"

    runtime.generation_prefix_cache.set(
        runtime.generation_cache_epoch,
        user_input,
        snap,
    )
    assert runtime.generation_prefix_cache.get(runtime.generation_cache_epoch, user_input) is not None

    # Update difficulty -> should invalidate cache and increment epoch
    save_difficulty({"difficulty": "简单"})
    assert runtime.generation_prefix_cache.get(runtime.generation_cache_epoch, user_input) is None

    # Re-populate and test save_experience_mode
    runtime.generation_prefix_cache.set(
        runtime.generation_cache_epoch,
        user_input,
        snap,
    )
    assert runtime.generation_prefix_cache.get(runtime.generation_cache_epoch, user_input) is not None

    save_experience_mode({"experience_mode": "creator"})
    assert runtime.generation_prefix_cache.get(runtime.generation_cache_epoch, user_input) is None


def test_record_update_invalidates_prefix_cache():
    from app.domain.continuity.schemas import ContinuityItem
    from app.domain.npc_profile.schemas import NPCProfile
    from app.domain.world_setting.schemas import WorldSetting
    from app.runtime.player_records import update_player_record
    from app.runtime.session import get_campaign_runtime

    runtime = get_campaign_runtime()
    # Seed records
    with runtime.stores.continuity as store:
        with store.transaction():
            store.insert(ContinuityItem(
                kind="commitment",
                subject="找回木偶",
                premise="帮助埃利奥找回被窃的木偶。",
                entities=["埃利奥"],
                created_turn=1,
                updated_turn=1,
            ))
    with runtime.stores.npc_profile as store:
        npc_id = store.upsert(NPCProfile(
            name="埃利奥",
            role="木偶师",
        ))
    with runtime.stores.world_setting as store:
        setting_id = store.save(WorldSetting(
            kind="location",
            name="灰烬图书馆",
            summary="石砌图书馆",
            public_facts=["一座古老的图书馆"],
            created_turn=1,
        ))

    snap = _make_snapshot()
    user_input = "记录变更失效测试"

    # Test NPC update invalidates
    runtime.generation_prefix_cache.set(runtime.generation_cache_epoch, user_input, snap)
    assert runtime.generation_prefix_cache.get(runtime.generation_cache_epoch, user_input) is not None
    update_player_record("npc", npc_id, {"role": "高级木偶师"})
    assert runtime.generation_prefix_cache.get(runtime.generation_cache_epoch, user_input) is None

    # Test setting update invalidates
    runtime.generation_prefix_cache.set(runtime.generation_cache_epoch, user_input, snap)
    assert runtime.generation_prefix_cache.get(runtime.generation_cache_epoch, user_input) is not None
    update_player_record("setting", setting_id, {"summary": "翻新后的图书馆"})
    assert runtime.generation_prefix_cache.get(runtime.generation_cache_epoch, user_input) is None


def test_pending_journal_roundtrip_preserves_prefix_snapshot():
    from app.domain.action.schemas import Anchor
    from app.domain.context_facts.schemas import (
        ContextHistoryEvidenceSet,
        ContextHistoryHit,
        ContextSearchEntity,
        ContextSearchIntent,
        ContextSearchPlan,
    )
    from app.domain.continuity.schemas import ContinuityItem, ContinuitySelection
    from app.domain.narrative_composer.schemas import NarrativeComposerContext
    from app.domain.npc_profile.schemas import NPCProfile, NPCProfileDirectorView
    from app.domain.plot_outline.schemas import PlotOutlineResult
    from app.domain.scene_writer.schemas import SceneWriterResult
    from app.domain.short_term_memory import ShortTermMemory
    from app.domain.action_judgment.schemas import PlayerTurnResolution
    from app.runtime.session import PendingTurn, get_campaign_runtime

    runtime = get_campaign_runtime()
    snap = _make_snapshot()
    snap.matched_anchors = [
        Anchor(text="老橡树镇", normalized="老橡树镇", kind="location", source="explicit", confidence=1.0)
    ]
    snap.action_npc_profiles = [
        NPCProfile(name="埃利奥", role="木偶师")
    ]
    snap.action_npc_director_views = [
        NPCProfile(name="埃利奥", role="木偶师").director_view()
    ]
    snap.context_fact_npc_profiles = [
        NPCProfile(name="埃利奥", role="木偶师")
    ]
    snap.context_search_plan = ContextSearchPlan(
        intents=[
            ContextSearchIntent(
                intent_id="i1",
                focus="topic",
                entities=[ContextSearchEntity(name="埃利奥", aliases=["木偶师"])],
                topics=["木偶"],
            )
        ],
    )
    snap.context_history_evidence = ContextHistoryEvidenceSet(
        hits=[
            ContextHistoryHit(
                evidence_id="ev_1",
                source_type="scene_paragraph",
                turn_index=1,
                scene_id="scene_1",
                text="找到木偶",
                score=1.0,
                matched_intent_ids=["i1"],
            )
        ],
    )
    from app.domain.continuity.schemas import ContinuityItem, ContinuityMatch, ContinuitySelection
    snap.continuity_selection = ContinuitySelection(
        items=[
            ContinuityMatch(
                item=ContinuityItem(kind="commitment", subject="找回木偶", premise="线索", entities=["埃利奥"], created_turn=1, updated_turn=1),
                score=1.0,
            )
        ]
    )

    turn_id = "turn_journal_snap_test"
    pending = PendingTurn(
        turn_id=turn_id,
        group_id="group_j1",
        version_index=1,
        user_input="持久化测试动作",
        difficulty="困难",
        max_chars=1500,
        experience_mode="player",
        state_before=_make_state(),
        context=NarrativeComposerContext(
            user_input="持久化测试动作",
            current_state=_make_state(),
            action_judgments=[],
            core_memory=CoreMemory(items=[]),
            context_facts=snap.context_facts,
        ),
        outline=PlotOutlineResult(scene_stopping_point="停顿点"),
        scene_result=SceneWriterResult(scene_text="持久化正文"),
        turn_index=1,
        short_term_memory=ShortTermMemory(),
        player_resolution=PlayerTurnResolution(),
        prefix_snapshot=snap,
    )

    try:
        # Add to pending_turns (which invokes _persist_pending_turn)
        runtime.pending_turns.add(pending)

        # Clear memory pending turns without deleting journal
        runtime.pending_turns.clear(discard=False)
        assert turn_id not in runtime.pending_turns

        # Restore from transactions
        runtime._restore_pending_transactions()
        restored = runtime.pending_turns.get(turn_id)
        assert restored is not None
        restored_snapshot = restored.prefix_snapshot
        assert restored_snapshot is not None
        assert restored_snapshot.action_parse_result.atomic_actions[0].action_id == "action_1"
        assert restored_snapshot.context_facts.turn_focus == "测试焦点"
        # The restored snapshot must contain typed domain models, not raw dicts,
        # because reroll downstream nodes call profile.view()/disclosure_view().
        assert isinstance(restored_snapshot.context_search_plan, ContextSearchPlan)
        assert isinstance(restored_snapshot.context_history_evidence, ContextHistoryEvidenceSet)
        assert isinstance(restored_snapshot.continuity_selection, ContinuitySelection)
        assert isinstance(restored_snapshot.action_npc_profiles[0], NPCProfile)
        assert restored_snapshot.action_npc_profiles[0].disclosure_view() is not None
        assert isinstance(restored_snapshot.action_npc_director_views[0], NPCProfileDirectorView)
        assert isinstance(restored_snapshot.context_fact_npc_profiles[0], NPCProfile)
        assert isinstance(restored_snapshot.matched_anchors[0], Anchor)
    finally:
        runtime.pending_turns.remove(turn_id)


def test_modifier_fallback_reroll_retries_modifier_evaluator_while_reusing_prefix(
    monkeypatch,
    configure_runtime_services,
):
    from app.domain.narrative_composer.schemas import NarrativeComposerContext
    from app.domain.plot_outline.schemas import PlotOutlineResult
    from app.domain.scene_writer.schemas import SceneWriterResult
    from app.domain.short_term_memory import ShortTermMemory
    from app.domain.action_judgment.schemas import PlayerTurnResolution
    from app.graph import generation_graph, generation_nodes
    from app.runtime.session import PendingTurn, get_campaign_runtime

    runtime = get_campaign_runtime()
    runtime.stores.core_memory.save(CoreMemory(items=[]))
    turn_id = "turn_mod_fallback_test"
    state_before = _make_state()
    # Snapshot where modifier_batch is None (cleared due to roll_modifier_fallback)
    snap = _make_snapshot(needs_roll=True)
    snap.modifier_batch = None
    snap.has_fallback = False

    pending = PendingTurn(
        turn_id=turn_id,
        group_id="group_mod_fallback",
        version_index=1,
        user_input="攻击敌人",
        difficulty="困难",
        max_chars=1500,
        experience_mode="player",
        state_before=state_before,
        context=NarrativeComposerContext(
            user_input="攻击敌人",
            current_state=state_before,
            action_judgments=[],
            core_memory=CoreMemory(items=[]),
            context_facts=snap.context_facts,
        ),
        outline=PlotOutlineResult(scene_stopping_point="停顿点"),
        scene_result=SceneWriterResult(scene_text="正文"),
        turn_index=1,
        short_term_memory=ShortTermMemory(),
        player_resolution=PlayerTurnResolution(),
        prefix_snapshot=snap,
    )
    runtime.pending_turns.add(pending)

    class _FakeStateStore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def load_or_default(self):
            return state_before

    configure_runtime_services(current_state=_FakeStateStore)

    modifier_called = []

    def _fake_eval_modifier(state):
        modifier_called.append(True)
        return {
            "modifier_batch": RollModifierBatch(),
            "roll_results": RollResultBatch(dice_formula="1d100", dice_sides=100, total_rolls=1, results=[]),
            "events": [{"step": "modifiers_done", "data": {}}],
        }

    monkeypatch.setattr(generation_nodes, "evaluate_roll_modifiers_node", _fake_eval_modifier)

    captured = {}

    def _fake_stream(state, graph, **kwargs):
        captured["state"] = state
        # Simulate graph running resolve_rolls_node
        node_res = generation_nodes.resolve_rolls_node(state)
        captured["node_res"] = node_res
        yield {"step": "scene_written", "data": {"turn_id": "turn_v2"}}

    monkeypatch.setattr(generation_graph, "_stream_generation_graph", _fake_stream)

    try:
        events = list(generation_graph.reroll_turn_stream(turn_id))
        assert events[0]["step"] == "reroll_started"
        # Verify modifier evaluation was triggered because modifier_batch was None
        assert modifier_called == [True]
        # Verify action and context were preserved from prefix snapshot
        assert captured["state"]["action_parse_result"] == snap.action_parse_result
        assert captured["state"]["context_facts"] == snap.context_facts
    finally:
        runtime.pending_turns.remove(turn_id)

