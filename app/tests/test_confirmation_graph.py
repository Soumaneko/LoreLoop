from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest
from langgraph.graph import END, START, StateGraph

from app.core.diagnostics import DiagnosticLogger
from app.domain.continuity import ContinuityProjection
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.current_state.changes import StateChangeSet, StateChangeWarning
from app.domain.scene_facts.schemas import SceneFact, SceneFactSet
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_summary.summarizer import SceneSummaryWarning
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.graph import confirmation_nodes
from app.graph.confirmation_graph import (
    build_confirmation_first_wave_graph,
    build_confirmation_second_wave_graph,
    capture_confirmation_node_errors,
)
from app.graph import confirmation_graph
from app.graph.confirmation_cache import ConfirmationCache
from app.graph.confirmation_helpers import merge_confirmation_node_update
from app.graph.confirmation_helpers import prepare_confirmation_second_wave
from app.graph.confirmation_state import ConfirmationState
from app.graph.confirmation_lifecycle import (
    ConfirmationFinalizer,
)


def test_confirmation_first_wave_preserves_parallel_extract_dependencies():
    graph = build_confirmation_first_wave_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("scene_fact_extract", "state_update") in edges
    assert ("state_update", "scene_summarize") in edges
    assert ("scene_summarize", "__end__") in edges
    assert all("npc_profile_extract" not in (source, target) for source, target in edges)


def test_state_update_warning_is_emitted_cached_and_diagnosed(
    monkeypatch,
    tmp_path,
):
    current_state = CurrentState(
        scene=SceneState(scene_id="scene-1", location="门外"),
        player=PlayerState(name="主角"),
    )
    warning = StateChangeWarning(
        operation_id="op2",
        operation_type="participant_attitude_change",
        target="米莉",
        code="npc_target_not_present",
        message="target missing",
    )
    change_set = StateChangeSet(decision="NPC 已离开。")

    class _Updater:
        def __init__(self, _gateway):
            self.last_change_set = change_set
            self.last_warnings = [warning]

        def update(self, state, *_args, **_kwargs):
            return state

    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _Updater)
    monkeypatch.setattr(confirmation_nodes, "_gateway_for", lambda _state: object())
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    state = {
        "pipeline_input": object(),
        "trace_id": "trace-1",
        "cache": {},
        "state_before_update": current_state,
        "scene_facts_json": "{}",
        "core_memory": CoreMemory(items=[]),
        "existing_npc_profiles": [],
        "events": [],
        "node_errors": [],
        "write_db": True,
        "runtime_services": SimpleNamespace(diagnostics=diagnostics),
    }

    update = confirmation_nodes.state_update_node(state)
    events = merge_confirmation_node_update(state, update)

    assert update["state_update_warnings"] == [warning]
    assert events[0]["data"]["warnings"][0]["code"] == "npc_target_not_present"
    assert state["cache"]["state_update_warnings"] == [warning]
    diagnostic = diagnostics.read(trace_id="trace-1")[0]
    assert diagnostic["source"] == "state_update"
    assert diagnostic["code"] == "npc_target_not_present"


def test_scene_summarize_warning_is_emitted_and_diagnosed(
    monkeypatch,
    tmp_path,
):
    summary = SceneSummary(
        id="summary-current",
        level=1,
        content="本轮摘要",
        scene_ids=["scene-current"],
        turn_start=6,
        turn_end=6,
    )
    warning = SceneSummaryWarning(
        code="scene_summary_fallback",
        message="No usable turn summary candidate; using placeholder.",
    )

    class _Summarizer:
        def __init__(self, _gateway):
            self.last_warnings = [warning]

        def summarize_turn(self, **_kwargs):
            return summary

    monkeypatch.setattr(confirmation_nodes, "SceneSummarizer", _Summarizer)
    monkeypatch.setattr(confirmation_nodes, "_gateway_for", lambda _state: object())
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    state = {
        "pipeline_input": SimpleNamespace(turn_index=6),
        "trace_id": "trace-summary",
        "cache": {},
        "confirmation_scene_text": "确认正文",
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene-current", location="新地点"),
            player=PlayerState(name="主角"),
        ),
        "core_memory": CoreMemory(items=[]),
        "runtime_services": SimpleNamespace(diagnostics=diagnostics),
    }

    result = confirmation_nodes.scene_summarize_node(state)

    assert result["turn_summary"] is summary
    diagnostic = diagnostics.read(trace_id="trace-summary")[0]
    assert diagnostic["source"] == "scene_summary"
    assert diagnostic["code"] == "scene_summary_fallback"


def test_only_selection_summaries_append_current_turn_summary():
    historical = [
        SceneSummary(
            id=f"summary-{index}",
            level=1,
            content=f"摘要 {index}",
            turn_start=index,
            turn_end=index,
        )
        for index in range(1, 6)
    ]
    current = SceneSummary(
        id="summary-6",
        level=1,
        content="本轮摘要",
        turn_start=6,
        turn_end=6,
    )

    class _Store:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_active(self):
            return historical

        def get_recent(self, limit):
            assert limit == 5
            return historical

    state = {
        "cache": {},
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene_006", location="测试地点"),
            player=PlayerState(name="主角"),
        ),
        "turn_summary": current,
        "runtime_services": SimpleNamespace(
            stores=SimpleNamespace(scene_summary=_Store)
        ),
        "events": [],
    }

    prepared = prepare_confirmation_second_wave(state)

    assert prepared["recent_summaries"] == historical
    assert prepared["scene_summaries"] == historical
    assert prepared["selection_summaries"] == [*historical, current]


def test_scene_summarize_uses_updated_state_and_confirmation_text(monkeypatch):
    captured = {}
    summary = SceneSummary(
        id="summary-current",
        level=1,
        content="本轮摘要",
        scene_ids=["scene-current"],
        turn_start=6,
        turn_end=6,
    )

    class _Summarizer:
        def __init__(self, _gateway):
            pass

        def summarize_turn(self, **kwargs):
            captured.update(kwargs)
            return summary

    updated_state = CurrentState(
        scene=SceneState(scene_id="scene-current", location="新地点"),
        player=PlayerState(name="主角"),
    )
    monkeypatch.setattr(confirmation_nodes, "SceneSummarizer", _Summarizer)
    result = confirmation_nodes.scene_summarize_node({
        "pipeline_input": SimpleNamespace(turn_index=6),
        "runtime_services": SimpleNamespace(
            gateway=lambda **_kwargs: object()
        ),
        "cache": {},
        "confirmation_scene_text": "确认正文",
        "updated_state": updated_state,
        "core_memory": CoreMemory(items=[]),
        "trace_id": "trace-6",
        "scene_facts": object(),
    })

    assert result["turn_summary"] is summary
    assert captured["scene_text"] == "确认正文"
    assert captured["current_state"] is updated_state
    assert "scene_facts" not in captured


def test_journal_completes_before_any_maintenance_is_scheduled():
    calls = []
    current_state = CurrentState(
        scene=SceneState(scene_id="scene_001", location="测试地点"),
        player=PlayerState(name="主角"),
    )

    class _Committer:
        def commit(self, _state):
            calls.append("business_commit")
            return object()

    class _Scheduler:
        def schedule_scene_summary(self, _state):
            calls.append("followups")

    class _EventBuilder:
        def build(self, _state, _commit_result):
            calls.append("events")
            return []

    runtime = SimpleNamespace(
        history=SimpleNamespace(
            record_state_audit=lambda *_args, **_kwargs: calls.append("audit")
        ),
        complete_confirmation=lambda _pending: calls.append("journal_completed"),
        pending_turns=SimpleNamespace(
            discard_group=lambda _group_id: calls.append("pending_removed")
        ),
        pending_openings=SimpleNamespace(
            discard_group=lambda _group_id: calls.append("pending_removed")
        ),
    )
    state = {
        "write_db": True,
        "journaled": True,
        "turn_id": "turn-1",
        "pending": SimpleNamespace(group_id="opening-group"),
        "pipeline_input": SimpleNamespace(state_before=current_state),
        "state_change_set": {},
        "updated_state": current_state,
        "cache": {},
        "events": [],
    }

    ConfirmationFinalizer(
        runtime,
        _Committer(),
        _Scheduler(),
        _EventBuilder(),
    ).finalize(state)

    assert calls == [
        "business_commit",
        "audit",
        "journal_completed",
        "pending_removed",
        "followups",
        "events",
    ]


def test_initialization_phase_is_revalidated_before_confirmation_writes():
    current_state = CurrentState(
        scene=SceneState(scene_id="scene_001", location="测试地点"),
        player=PlayerState(name="主角"),
    )
    commit_called = False

    class _Committer:
        def commit(self, _state):
            nonlocal commit_called
            commit_called = True
            return object()

    runtime = SimpleNamespace(
        initialization=SimpleNamespace(load=lambda: SimpleNamespace(
            fingerprint="fingerprint",
            phase="foundation_ready",
            foundation=object(),
        )),
    )
    state = {
        "write_db": True,
        "pending": SimpleNamespace(
            initialization_fingerprint="fingerprint",
        ),
        "pipeline_input": SimpleNamespace(
            state_before=current_state,
            complete_initialization=True,
        ),
    }

    with pytest.raises(
        RuntimeError,
        match="lost its initialization record",
    ):
        ConfirmationFinalizer(
            runtime,
            _Committer(),
            SimpleNamespace(),
            SimpleNamespace(),
        ).finalize(state)

    assert commit_called is False


def test_confirmation_second_wave_starts_after_helper_state_is_ready():
    graph = build_confirmation_second_wave_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("__start__", "action_preview") in edges
    assert ("__start__", "continuity_project") in edges
    assert ("__start__", "npc_profile_update") in edges
    assert ("__start__", "setting_appearance_update") in edges
    assert ("setting_appearance_update", "__end__") in edges

    director_graph = confirmation_graph.build_confirmation_director_graph().get_graph()
    director_edges = {(edge.source, edge.target) for edge in director_graph.edges}
    assert ("__start__", "director_event_review") in director_edges
    assert ("director_event_review", "director_agent_confirm") in director_edges
    assert ("director_agent_confirm", "__end__") in director_edges


def test_setting_appearance_node_counts_once_and_is_cached(monkeypatch, tmp_path):
    from app.domain.world_setting import WorldSetting, WorldSettingStore

    db_path = tmp_path / "world_settings.db"
    with WorldSettingStore(str(db_path)) as seed_store:
        library = WorldSetting(
            kind="location",
            name="灰烬图书馆",
            summary="一座石砌图书馆。",
            public_facts=["对登记学者开放。"],
            created_turn=1,
        )
        guild = WorldSetting(
            kind="faction",
            name="北陆商盟",
            summary="北境商会。",
            public_facts=["统一收取过路费。"],
            created_turn=1,
        )
        seed_store.save(library)
        seed_store.save(guild)

    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, name: lambda: WorldSettingStore(str(db_path)),
    )
    state = {
        "write_db": True,
        "effective_scene_text": "莱尔走进了灰烬图书馆的借阅室。",
        "cache": {},
    }

    result = confirmation_nodes.setting_appearance_update_node(state)
    assert result["recorded_setting_ids"] == [library.setting_id]
    merge_confirmation_node_update(state, result)
    assert state["cache"]["recorded_setting_ids"] == [library.setting_id]
    with WorldSettingStore(str(db_path)) as check:
        assert check.get_by_id(library.setting_id).hit_count == 1
        assert check.get_by_id(guild.setting_id).hit_count == 0

    replayed = confirmation_nodes.setting_appearance_update_node(state)
    assert replayed["recorded_setting_ids"] == [library.setting_id]
    with WorldSettingStore(str(db_path)) as check:
        assert check.get_by_id(library.setting_id).hit_count == 1


def test_setting_appearance_node_skips_when_not_writing_db(monkeypatch):
    from app.domain.world_setting import WorldSettingStore

    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, name: lambda: WorldSettingStore(":memory:"),
    )
    state = {
        "write_db": False,
        "effective_scene_text": "灰烬图书馆",
        "cache": {},
    }

    result = confirmation_nodes.setting_appearance_update_node(state)
    assert result == {"recorded_setting_ids": []}


def test_setting_appearance_node_ignores_single_character_names(monkeypatch, tmp_path):
    from app.domain.world_setting import WorldSetting, WorldSettingStore

    db_path = tmp_path / "world_settings.db"
    with WorldSettingStore(str(db_path)) as seed_store:
        single = WorldSetting(
            kind="lore",
            name="道",
            summary="修行之途。",
            public_facts=["道法自然。"],
            created_turn=1,
        )
        guild = WorldSetting(
            kind="faction",
            name="镖局",
            summary="押送货物的行会。",
            public_facts=["收取镖费。"],
            created_turn=1,
        )
        seed_store.save(single)
        seed_store.save(guild)

    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, name: lambda: WorldSettingStore(str(db_path)),
    )
    state = {
        "write_db": True,
        "effective_scene_text": "他知晓镖局的规矩。",
        "cache": {},
    }

    result = confirmation_nodes.setting_appearance_update_node(state)
    assert guild.setting_id in result["recorded_setting_ids"]
    assert single.setting_id not in result["recorded_setting_ids"]
    with WorldSettingStore(str(db_path)) as check:
        assert check.get_by_id(single.setting_id).hit_count == 0
        assert check.get_by_id(guild.setting_id).hit_count == 1


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
            "npc_turn_result": marker,
            "events": [{"step": "done", "data": {}}],
        },
    )

    assert state["cache"]["scene_facts"] is marker
    assert state["cache"]["npc_turn_result"] is marker
    assert state["events"] == [{"step": "done", "data": {}}]


def test_dry_run_merge_does_not_cache_write_only_results():
    marker = object()
    state = {"events": [], "cache": {}, "write_db": False}

    merge_confirmation_node_update(
        state,
        {
            "npc_turn_result": marker,
            "recorded_setting_ids": ["setting_1"],
        },
    )

    assert "npc_turn_result" not in state["cache"]
    assert "recorded_setting_ids" not in state["cache"]


def test_scene_fact_diagnostics_use_degraded_reasons_not_emptiness(
    monkeypatch,
    tmp_path,
):
    scene_facts = SceneFactSet(
        scene_focus="部分内容保留",
        facts=[SceneFact(fact_id="f1", fact="有效事实")],
        degraded=True,
        degraded_reasons=["npc_mentions"],
    )
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")

    class _Extractor:
        def __init__(self, _gateway):
            pass

        def extract(self, *_args, **_kwargs):
            return scene_facts

    monkeypatch.setattr(confirmation_nodes, "SceneFactExtractor", _Extractor)
    state = {
        "pipeline_input": SimpleNamespace(turn_index=1, user_input="输入"),
        "trace_id": "trace-1",
        "cache": {},
        "core_memory": CoreMemory(items=[]),
        "state_before_update": CurrentState(
            scene=SceneState(scene_id="scene-1", location="门外"),
            player=PlayerState(name="主角"),
        ),
        "effective_scene_text": "正文",
        "events": [],
        "runtime_services": SimpleNamespace(
            gateway=lambda **_kwargs: object(),
            diagnostics=diagnostics,
        ),
    }

    confirmation_nodes.scene_fact_extract_node(state)

    codes = {entry["code"] for entry in diagnostics.read()}
    assert codes == {"npc_mentions_degraded"}


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


def test_node_error_wrapper_reraise_cancellation():
    def cancelled(_state: ConfirmationState) -> ConfirmationState:
        raise CancelledError("user cancelled")

    graph = StateGraph(ConfirmationState)
    graph.add_node(
        "cancelled",
        capture_confirmation_node_errors("cancelled", cancelled),
    )
    graph.add_edge(START, "cancelled")
    graph.add_edge("cancelled", END)

    state = {"events": [], "node_errors": [], "cache": {}, "write_db": True}
    with pytest.raises(CancelledError):
        for update in graph.compile().stream(state, stream_mode="updates"):
            for node_update in update.values():
                merge_confirmation_node_update(state, node_update)

    assert state["node_errors"] == []


def test_rollback_cache_keeps_pure_results_and_discards_write_markers():
    marker = object()
    pending = SimpleNamespace(confirmation_cache={
        "key": "scene text",
        "scene_facts": marker,
        "updated_state": marker,
        "state_change_set": marker,
        "turn_summary": marker,
        "action_preview": marker,
        "npc_extraction": marker,
        "director_decision": marker,
        "director_executions": marker,
        "updated_npc_profile_ids": ["npc_1"],
        "director_npc_profile_ids": ["npc_2"],
        "state_saved": True,
        "short_term_saved": True,
        "confirmed_history_saved": True,
        "npc_field_logs_saved": True,
        "scene_summary_scheduled": True,
    })

    ConfirmationCache(pending.confirmation_cache).reset_for_rollback()

    assert pending.confirmation_cache == {
        "key": "scene text",
        "scene_facts": marker,
        "updated_state": marker,
        "state_change_set": marker,
        "turn_summary": marker,
        "action_preview": marker,
    }


def test_journaled_confirmation_failure_resets_rolled_back_cache(monkeypatch):
    marker = object()
    pending = SimpleNamespace(confirmation_cache={
        "key": "scene text",
        "scene_facts": marker,
        "updated_npc_profile_ids": ["npc_1"],
        "state_saved": True,
    })
    calls = []
    runtime = SimpleNamespace(
        begin_confirmation=lambda *_args, **_kwargs: True,
        jobs=SimpleNamespace(wait_for_idle=lambda: calls.append("wait")),
        history=SimpleNamespace(
            fail_confirmation=lambda *_args: calls.append("rollback")
        ),
    )

    def fail_pipeline(*_args, **_kwargs):
        raise RuntimeError("node failed")
        yield

    monkeypatch.setattr(confirmation_graph, "get_campaign_runtime", lambda: runtime)
    monkeypatch.setattr(
        confirmation_graph,
        "stream_confirmation_pipeline",
        fail_pipeline,
    )

    with pytest.raises(RuntimeError, match="node failed"):
        list(confirmation_graph._confirm_pipeline_locked(
            SimpleNamespace(turn_id="turn-1", pending=pending),
        ))

    assert calls == ["wait", "rollback"]
    assert pending.confirmation_cache == {
        "key": "scene text",
        "scene_facts": marker,
    }


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


def test_staged_director_npc_promotes_without_preadding_participants(
    configure_runtime_services,
):
    from app.domain.director import DirectorStageMaterial, DirectorStageResult
    from app.domain.director_npc import DirectorNPCDraft, KnownAbility
    from app.graph.confirmation_helpers import prepare_confirmation_state
    from app.graph.confirmation_state import ConfirmationPipelineInput
    from app.runtime.session import PendingTurn, get_campaign_runtime

    services = configure_runtime_services()
    state = CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="测试地点",
            participants=["主角"],
        ),
        player=PlayerState(name="主角"),
    )
    with services.stores.current_state() as store:
        store.save(state)
    services.stores.core_memory().save(CoreMemory(items=[]))

    draft = DirectorNPCDraft(
        name="林澈",
        role="社区医生",
        appearance="常穿深色风衣。",
        personality_trait="见人先问诊，习惯主动掌握对方近况",
        known_abilities=[KnownAbility(name="急救", description="处理外伤与常见急症")],
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        conflict_style="先协商后行动。",
        social_style="克制而友善。",
        created_turn=1,
    )
    with services.stores.director_npc() as store:
        store.save(draft)

    scene_result = SceneWriterResult(scene_text="林澈走进来。")
    pending = PendingTurn(
        turn_id="turn-staged",
        group_id="turn-staged",
        version_index=1,
        user_input="让林澈进来。",
        difficulty="困难",
        max_chars=1500,
        experience_mode="creator",
        state_before=state,
        context=SimpleNamespace(turn_resolution=SimpleNamespace()),
        outline=SimpleNamespace(),
        scene_result=scene_result,
        director_stage=DirectorStageResult(
            reasoning="引入林澈。",
            materials=[
                DirectorStageMaterial(
                    kind="npc",
                    resource_id=draft.draft_id,
                    guidance="以林澈视角互动。",
                    payload={"source": "director_npc", "name": "林澈"},
                )
            ],
        ),
    )
    prepared = prepare_confirmation_state(
        ConfirmationPipelineInput(
            turn_id="turn-staged",
            pending=pending,
            state_before=state,
            scene_result=scene_result,
            user_input="让林澈进来。",
            turn_index=2,
            stale_message="stale",
        ),
        write_db=True,
    )

    assert prepared["confirmed_director_npc_drafts"][0].draft_id == draft.draft_id
    assert {p.name for p in prepared["state_before_update"].scene.participants} == {
        "主角"
    }
    assert "林澈" in prepared["confirmation_scene_text"]
    with services.stores.director_npc() as store:
        saved = store.get_by_id(draft.draft_id)
    assert saved.status == "introduced"
    with services.stores.npc_profile() as store:
        assert [profile.name for profile in store.list_all()] == ["林澈"]


class _FakeStore:
    def __init__(self, active=None):
        self._active = active or []

    def list_active(self):
        return self._active

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _continuity_node_state(*, cache, scene_text="正文", clear=False):
    return {
        "pipeline_input": SimpleNamespace(
            turn_index=1,
            user_input="",
            clear_continuity_before_save=clear,
        ),
        "updated_state": SimpleNamespace(
            scene=SimpleNamespace(scene_id="scene_001")
        ),
        "effective_scene_text": scene_text,
        "trace_id": "trace-1",
        "cache": cache,
        "runtime_services": SimpleNamespace(gateway=lambda **kw: object()),
    }


def test_continuity_project_node_reuses_cached_projection(monkeypatch):
    cached = ContinuityProjection(operations=[])
    state = _continuity_node_state(cache={"continuity_projection": cached})
    calls = []

    class _Projector:
        def __init__(self, *_args, **_kwargs):
            pass

        def project(self, **_kwargs):
            calls.append("project")
            return ContinuityProjection(operations=[])

    monkeypatch.setattr(confirmation_nodes, "ContinuityProjector", _Projector)
    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, _name: _FakeStore,
    )

    result = confirmation_nodes.continuity_project_node(state)

    assert result["continuity_projection"] is cached
    assert calls == []
    assert result["events"][0]["data"]["active_count"] == 0


def test_continuity_project_node_computes_and_caches_projection(monkeypatch):
    state = _continuity_node_state(cache={"key": "正文"})
    fresh = ContinuityProjection(operations=[])

    class _Projector:
        def __init__(self, *_args, **_kwargs):
            pass

        def project(self, **_kwargs):
            return fresh

    monkeypatch.setattr(confirmation_nodes, "ContinuityProjector", _Projector)
    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, _name: _FakeStore,
    )

    result = confirmation_nodes.continuity_project_node(state)

    assert result["continuity_projection"] is fresh
    assert state["cache"]["continuity_projection"] is fresh


def test_continuity_project_node_emits_warning(monkeypatch, tmp_path):
    state = _continuity_node_state(cache={"key": "正文"})
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    state["runtime_services"] = SimpleNamespace(
        gateway=lambda **_kwargs: object(),
        diagnostics=diagnostics,
    )

    class _Projector:
        def __init__(self, *_args, **_kwargs):
            self.last_warnings = [SimpleNamespace(
                code="continuity_fallback",
                message="fallback",
            )]

        def project(self, **_kwargs):
            return ContinuityProjection(operations=[])

    monkeypatch.setattr(confirmation_nodes, "ContinuityProjector", _Projector)
    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, _name: _FakeStore,
    )

    confirmation_nodes.continuity_project_node(state)

    diagnostic = diagnostics.read(trace_id="trace-1")[0]
    assert diagnostic["source"] == "continuity"
    assert diagnostic["code"] == "continuity_fallback"


def test_action_preview_node_emits_warning(monkeypatch, tmp_path):
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    state = {
        "pipeline_input": SimpleNamespace(
            turn_index=1,
            scene_result=SimpleNamespace(
                scene_text="正文",
                next_action_directions=[],
            ),
        ),
        "trace_id": "trace-action",
        "cache": {},
        "effective_scene_text": "正文",
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene_001", location="新地点"),
            player=PlayerState(name="主角"),
        ),
        "core_memory": CoreMemory(items=[]),
        "recent_summaries": [],
        "scene_facts": SceneFactSet(scene_focus="x", facts=[]),
        "runtime_services": SimpleNamespace(
            gateway=lambda **_kwargs: object(),
            diagnostics=diagnostics,
        ),
    }

    class _Agent:
        def __init__(self, _gateway):
            self.last_warnings = [SimpleNamespace(
                code="action_preview_fallback",
                message="fallback",
            )]

        def generate(self, **_kwargs):
            return SimpleNamespace(previews=[])

    monkeypatch.setattr(confirmation_nodes, "ActionPreviewAgent", _Agent)

    confirmation_nodes.action_preview_node(state)

    diagnostic = diagnostics.read(trace_id="trace-action")[0]
    assert diagnostic["source"] == "action_preview"
    assert diagnostic["code"] == "action_preview_fallback"


def _state_update_node_state(*, cache):
    return {
        "pipeline_input": SimpleNamespace(turn_index=1),
        "trace_id": "trace-1",
        "cache": cache,
        "state_before_update": SimpleNamespace(),
        "scene_facts_json": "{}",
        "core_memory": CoreMemory(items=[]),
        "existing_npc_profiles": [],
        "events": [],
        "node_errors": [],
        "write_db": True,
        "runtime_services": SimpleNamespace(gateway=lambda **kw: object()),
    }


def test_state_update_node_recomputes_all_fields_when_cache_partial(monkeypatch):
    cached_state = "cached-state"
    state = _state_update_node_state(cache={"updated_state": cached_state})
    update_calls = []

    class _Updater:
        def __init__(self, *_args, **_kwargs):
            self.last_change_set = StateChangeSet(decision="fresh set")
            self.last_warnings = [StateChangeWarning(
                operation_id="op1",
                operation_type="participant_attitude_change",
                target="米莉",
                code="w1",
                message="fresh",
            )]

        def update(self, *_args, **_kwargs):
            update_calls.append("update")
            return "fresh-state"

    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _Updater)

    result = confirmation_nodes.state_update_node(state)

    assert update_calls == ["update"]
    assert result["updated_state"] == "fresh-state"
    assert result["state_change_set"].decision == "fresh set"
    assert result["state_update_warnings"][0].message == "fresh"


def test_state_update_node_reuses_complete_triple(monkeypatch):
    state = _state_update_node_state(cache={
        "updated_state": "cached-state",
        "state_change_set": StateChangeSet(decision="cached set"),
        "state_update_warnings": [],
    })
    update_calls = []

    class _Updater:
        def __init__(self, *_args, **_kwargs):
            pass

        def update(self, *_args, **_kwargs):
            update_calls.append("update")
            return "fresh-state"

    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _Updater)

    result = confirmation_nodes.state_update_node(state)

    assert update_calls == []
    assert result["updated_state"] == "cached-state"
    assert result["state_change_set"].decision == "cached set"


def _npc_node_state(**overrides):
    state = {
        "pipeline_input": SimpleNamespace(turn_index=1, user_input=""),
        "trace_id": "trace-1",
        "cache": {"key": "正文"},
        "write_db": True,
        "scene_facts": SceneFactSet(scene_focus="裴玉奴出现在醉花楼。", facts=[]),
        "updated_state": CurrentState(
            scene=SceneState(
                scene_id="scene_001",
                location="醉花楼",
                participants=["萧云痕"],
            ),
            player=PlayerState(name="萧云痕"),
        ),
        "effective_scene_text": "裴玉奴出现在醉花楼。",
        "core_memory": CoreMemory(items=[]),
        "existing_npc_profiles": [],
        "runtime_services": SimpleNamespace(gateway=lambda **kw: object()),
    }
    state.update(overrides)
    return state


def _fake_process(saved_ids=("npc_fresh",), candidates=(), warnings=()):
    return SimpleNamespace(
        saved_ids=list(saved_ids),
        new_npc_candidates=list(candidates),
        warnings=list(warnings),
    )


def _patch_npc_pipeline(monkeypatch):
    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, _name: _FakeStore,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "_director_draft_ids",
        lambda _state: set(),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "_candidate_shells",
        lambda candidates, **kwargs: ([], {}),
    )


def test_candidate_npc_id_is_deterministic_and_conflict_free():
    from app.domain.npc_profile.identity_resolver import NpcCandidate

    first = confirmation_nodes._candidate_npc_id(
        scene_id="scene_001",
        turn_index=3,
        primary_surface="采药女",
        occupied_ids=set(),
    )
    second = confirmation_nodes._candidate_npc_id(
        scene_id="scene_001",
        turn_index=3,
        primary_surface="采药女",
        occupied_ids=set(),
    )
    assert first == second
    assert first.startswith("cand_")

    shifted = confirmation_nodes._candidate_npc_id(
        scene_id="scene_001",
        turn_index=4,
        primary_surface="采药女",
        occupied_ids=set(),
    )
    assert shifted != first

    avoided = confirmation_nodes._candidate_npc_id(
        scene_id="scene_001",
        turn_index=3,
        primary_surface="采药女",
        occupied_ids={first},
    )
    assert avoided != first

    shells, context = confirmation_nodes._candidate_shells(
        [NpcCandidate(
            primary_surface="采药女",
            presence="appeared",
            evidence_fact_ids=("f1",),
        )],
        scene_id="scene_001",
        turn_index=3,
        admitted_at="2026-08-12T00:00:00Z",
        existing_profile_ids={first},
        director_draft_ids=set(),
    )
    assert shells[0].name == "采药女"
    assert shells[0].npc_id == avoided
    assert shells[0].source_scene_ids == ["scene_001"]
    assert shells[0].created_turn == 3
    assert shells[0].introduced_turn == 3
    assert shells[0].updated_at == "2026-08-12T00:00:00Z"
    assert context[avoided].alias_surfaces == []
    assert context[avoided].evidence_fact_ids == ["f1"]


def test_npc_profile_update_node_recomputes_when_slot_missing(monkeypatch):
    state = _npc_node_state()
    _patch_npc_pipeline(monkeypatch)
    update_calls = []

    def _apply(**kwargs):
        update_calls.append("update")
        return _fake_process()

    monkeypatch.setattr(confirmation_nodes, "apply_npc_batch_update", _apply)
    monkeypatch.setattr(
        confirmation_nodes,
        "prune_npc_transient_aliases_from_current_state",
        lambda **kwargs: ["npc_pruned"],
    )

    result = confirmation_nodes.npc_profile_update_node(state)

    assert update_calls == ["update"]
    assert result["updated_npc_profile_ids"] == ["npc_fresh", "npc_pruned"]
    assert result["new_npc_profiles"] == []
    assert result["npc_candidate_context"] == {}
    assert state["cache"]["npc_turn_result"].updated_npc_profile_ids == [
        "npc_fresh",
        "npc_pruned",
    ]


def test_npc_profile_update_node_emits_warning_summary_event(monkeypatch):
    from app.domain.npc_profile.changes import NPCChangeAdmissionWarning

    warnings = [
        NPCChangeAdmissionWarning(
            npc_name="裴玉奴",
            field_name="public_summary",
            code="long_term_field_evidence_missing",
            message="dropped",
        )
    ]
    state = _npc_node_state()
    _patch_npc_pipeline(monkeypatch)
    monkeypatch.setattr(
        confirmation_nodes,
        "apply_npc_batch_update",
        lambda **kwargs: _fake_process(warnings=warnings),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "prune_npc_transient_aliases_from_current_state",
        lambda **kwargs: [],
    )

    result = confirmation_nodes.npc_profile_update_node(state)

    assert result["events"] == [{
        "step": "npc_profile_updated",
        "data": {
            "updated_npc_profile_ids": ["npc_fresh"],
            "new_npc_candidates": [],
            "warnings": [{
                "npc_name": "裴玉奴",
                "field": "public_summary",
                "code": "long_term_field_evidence_missing",
                "evidence_ids": [],
            }],
        },
    }]
    assert result["npc_warnings"] == [{
        "npc_name": "裴玉奴",
        "field": "public_summary",
        "code": "long_term_field_evidence_missing",
        "evidence_ids": [],
    }]


def test_npc_profile_update_node_reuses_cached_slot(monkeypatch):
    from app.graph.npc_result import NpcTurnNodeResult

    node_result = NpcTurnNodeResult(
        process_result=_fake_process(),
        updated_npc_profile_ids=["cached"],
        new_npc_profiles=[],
        candidate_context={},
    )
    state = _npc_node_state(cache={"key": "正文", "npc_turn_result": node_result})
    update_calls = []

    def _apply(**kwargs):
        update_calls.append("update")
        return _fake_process()

    monkeypatch.setattr(confirmation_nodes, "apply_npc_batch_update", _apply)

    result = confirmation_nodes.npc_profile_update_node(state)

    assert update_calls == []
    assert result["updated_npc_profile_ids"] == ["cached"]
    assert result["events"] == []
    assert result["npc_warnings"] == []


def test_npc_profile_update_node_cache_hit_skips_diagnostics(monkeypatch):
    from app.domain.npc_profile.changes import NPCChangeAdmissionWarning
    from app.graph.npc_result import NpcTurnNodeResult

    logged = []

    class _Recorder:
        def warning(self, **fields):
            logged.append(fields)

    node_result = NpcTurnNodeResult(
        process_result=_fake_process(warnings=[
            NPCChangeAdmissionWarning(
                npc_name="裴玉奴",
                field_name="faction",
                code="long_term_field_evidence_missing",
                message="dropped",
            )
        ]),
        updated_npc_profile_ids=["cached"],
        new_npc_profiles=[],
        candidate_context={},
    )
    state = _npc_node_state(
        cache={"key": "正文", "npc_turn_result": node_result},
        runtime_services=SimpleNamespace(diagnostics=_Recorder()),
    )

    def _apply(**kwargs):
        raise AssertionError("factory must not run on a cache hit")

    monkeypatch.setattr(confirmation_nodes, "apply_npc_batch_update", _apply)

    result = confirmation_nodes.npc_profile_update_node(state)

    assert result["events"] == []
    assert logged == []
    assert result["npc_warnings"] == [{
        "npc_name": "裴玉奴",
        "field": "faction",
        "code": "long_term_field_evidence_missing",
        "evidence_ids": [],
    }]
