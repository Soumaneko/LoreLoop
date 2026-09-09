from app.tests.factories.stores import open_world_setting_store
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest
from langgraph.graph import END, START, StateGraph

from app.core.diagnostics import DiagnosticLogger
from app.domain.continuity import ContinuityProjection
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.current_state.changes import StateChangeSet, StateChangeWarning
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_summary.summarizer import SceneSummaryWarning
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.graph import confirmation_nodes
from app.graph.confirmation_graph import capture_confirmation_node_errors
from app.graph import confirmation_graph
from app.graph.confirmation_cache import ConfirmationCache
from app.graph.confirmation_helpers import merge_confirmation_node_update
from app.graph.confirmation_helpers import prepare_confirmation_second_wave
from app.graph.confirmation_state import ConfirmationState
from app.graph.confirmation_lifecycle import (
    ConfirmationFinalizer,
)
from app.runtime.sqlite import connect_sqlite


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
        "pipeline_input": SimpleNamespace(turn_index=1, user_input="推门而入"),
        "trace_id": "trace-1",
        "cache": {},
        "state_before_update": current_state,
        "effective_scene_text": "主角走入门内。",
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

    class _ContinuityStore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_active(self):
            return []

    state = {
        "cache": {},
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene_006", location="测试地点"),
            player=PlayerState(name="主角"),
        ),
        "turn_summary": current,
        "pipeline_input": SimpleNamespace(
            clear_continuity_before_save=False,
        ),
        "runtime_services": SimpleNamespace(
            stores=SimpleNamespace(
                scene_summary=_Store(),
                continuity=_ContinuityStore(),
            )
        ),
        "events": [],
    }

    prepared = prepare_confirmation_second_wave(state)

    assert prepared["recent_summaries"] == historical
    assert prepared["scene_summaries"] == historical
    assert prepared["selection_summaries"] == [*historical, current]
    assert prepared["active_continuity_items"] == []










def test_setting_appearance_node_dry_run_and_prevents_double_count(tmp_path):
    from app.domain.world_setting import WorldSetting

    db_path = tmp_path / "world_settings.db"
    with open_world_setting_store(str(db_path)) as seed_store:
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

    connection = connect_sqlite(db_path)
    state = {
        "write_db": True,
        "effective_scene_text": "莱尔走进了灰烬图书馆的借阅室。",
        "cache": {},
        "runtime_services": SimpleNamespace(
            stores=SimpleNamespace(
                world_setting=open_world_setting_store(connection)
            )
        ),
    }

    first = confirmation_nodes.setting_appearance_update_node(state)
    assert first["recorded_setting_ids"] == [library.setting_id]
    merge_confirmation_node_update(state, first)
    assert state["cache"]["recorded_setting_ids"] == [library.setting_id]
    with open_world_setting_store(str(db_path)) as check:
        assert check.get_by_id(library.setting_id).hit_count == 1
    connection.close()

    replayed = confirmation_nodes.setting_appearance_update_node(state)
    assert replayed["recorded_setting_ids"] == [library.setting_id]
    with open_world_setting_store(str(db_path)) as check:
        assert check.get_by_id(library.setting_id).hit_count == 1

    dry = confirmation_nodes.setting_appearance_update_node(
        {"write_db": False, "effective_scene_text": "灰烬图书馆", "cache": {}}
    )
    assert dry == {"recorded_setting_ids": []}
    with open_world_setting_store(str(db_path)) as check:
        assert check.get_by_id(library.setting_id).hit_count == 1






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
        fail_confirmation_offline=lambda *_args: calls.append("rollback"),
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


def test_journaled_confirmation_timeout_propagates_without_partial_commit(
    monkeypatch,
):
    from app.core.llm.errors import LLMTimeoutError

    pending = SimpleNamespace(confirmation_cache={
        "key": "scene text",
        "scene_facts": object(),
        "state_saved": True,
    })
    calls = []
    runtime = SimpleNamespace(
        begin_confirmation=lambda *_args, **_kwargs: True,
        jobs=SimpleNamespace(wait_for_idle=lambda: calls.append("wait")),
        fail_confirmation_offline=lambda *_args: calls.append("rollback"),
    )

    def timeout_pipeline(*_args, **_kwargs):
        raise LLMTimeoutError(
            "模型请求超过120秒，已停止等待。",
            timeout_seconds=120.0,
            task="scene_facts",
        )
        yield

    monkeypatch.setattr(confirmation_graph, "get_campaign_runtime", lambda: runtime)
    monkeypatch.setattr(
        confirmation_graph,
        "stream_confirmation_pipeline",
        timeout_pipeline,
    )

    with pytest.raises(LLMTimeoutError, match="已停止等待"):
        list(confirmation_graph._confirm_pipeline_locked(
            SimpleNamespace(turn_id="turn-1", pending=pending),
        ))

    assert calls == ["wait", "rollback"]
    assert pending.confirmation_cache == {
        "key": "scene text",
        "scene_facts": pending.confirmation_cache["scene_facts"],
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
    with services.stores.current_state as store:
        store.save(state)
    services.stores.core_memory.save(CoreMemory(items=[]))

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
    with services.stores.director_npc as store:
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
        context=SimpleNamespace(action_judgments=[]),
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
    with services.stores.director_npc as store:
        saved = store.get_by_id(draft.draft_id)
    assert saved.status == "introduced"
    with services.stores.npc_profile as store:
        assert [profile.name for profile in store.list_all()] == ["林澈"]
