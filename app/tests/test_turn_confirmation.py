from app.tests.factories.domains import make_pending_turn
from types import SimpleNamespace
import pytest
from app.domain.core_memory import CampaignSetupProposal, InitConfig
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.opening_foundation import OpeningFoundation
from app.domain.scene_writer import SceneWriterResult
from app.graph import confirmation_graph
from app.graph.confirmation_nodes import director_agent_confirm_node
from app.runtime.initialization import commit_campaign_setup
from app.runtime.session import PendingOpening, get_campaign_runtime


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="测试地点",
            participants=["主角"],
        ),
        player=PlayerState(name="主角"),
    )


def _opening_initialization(*, experience_mode: str):
    runtime = get_campaign_runtime()
    config = InitConfig(
        world_name="测试世界",
        world_style="奇幻",
        ability_system="魔法",
        protagonist_name="主角",
        story_expectation="探索并逐步成长。",
    )
    proposal = CampaignSetupProposal(
        currency_unit="金币",
        public_power_baseline={
            "source": "魔法",
            "prevalence": "少数人掌握",
            "learning_difficulty": "困难",
            "limitation": "不能复活死者。",
        },
        peoples_and_beings=["人类", "精灵"],
        major_world_conflicts=["帝国扩张持续挤压边境诸国。"],
        dominant_powers=["北方帝国", "圣辉教会"],
        civilization_baseline="以马车、冷兵器和手工业为主。",
    )
    record = commit_campaign_setup(config, proposal)
    record = record.model_copy(
        update={
            "phase": "opening_pending",
            "foundation": OpeningFoundation.model_validate(
                {
                    "starting_location": {
                        "name": "测试地点",
                        "description": "用于测试的地点。",
                    },
                    "protagonist_opening_situation": "主角已抵达测试地点。",
                    "initial_resources": {
                        "inventory": [],
                        "money": 5,
                        "fixed_assets": [],
                    },
                }
            ),
        }
    )
    runtime.initialization.save(record)
    with runtime.stores.current_state as state_store:
        state_store.save(_state())
    return config, record


def test_turn_confirmation_runs_director_pipeline_for_player_and_creator(campaign_runtime, monkeypatch):
    runtime = get_campaign_runtime()
    captured_modes = []

    def capture(pipeline_input, **_kwargs):
        captured_modes.append(pipeline_input.pending.experience_mode)
        return iter(())

    monkeypatch.setattr(confirmation_graph, "_confirm_pipeline_locked", capture)
    for mode in ("player", "creator"):
        turn_id = f"turn-{mode}"
        runtime.pending_turns.add(
            make_pending_turn(
                turn_id=turn_id,
                group_id=turn_id,
                version_index=1,
                user_input="观察四周",
                difficulty="困难",
                max_chars=1500,
                experience_mode=mode,
                state_before=_state(),
                scene_result=SceneWriterResult(scene_text="场景正文"),
            )
        )
        try:
            list(confirmation_graph._confirm_turn_stream_locked(turn_id))
        finally:
            runtime.pending_turns.remove(turn_id)

    # Both modes reach the full confirmation pipeline; Director always runs.
    assert captured_modes == ["player", "creator"]


def test_opening_confirmation_always_runs_director_and_reset_flags(campaign_runtime, monkeypatch):
    runtime = get_campaign_runtime()
    turn_id = "director-disabled-opening"
    config, record = _opening_initialization(experience_mode="player")
    runtime.pending_openings.add(
        PendingOpening(
            turn_id=turn_id,
            group_id=turn_id,
            version_index=1,
            initialization_fingerprint=record.fingerprint,
            config=config,
            experience_mode="player",
            current_state=_state(),
            scene_result=SceneWriterResult(scene_text="开场正文"),
        )
    )
    captured = {}

    def capture(pipeline_input, **_kwargs):
        captured["input"] = pipeline_input
        return iter(())

    monkeypatch.setattr(confirmation_graph, "_confirm_pipeline_locked", capture)
    try:
        list(confirmation_graph._confirm_opening_stream_locked(turn_id))
    finally:
        runtime.pending_openings.remove(turn_id)

    pipeline_input = captured["input"]
    assert pipeline_input.clear_short_term_before_save is True
    assert pipeline_input.clear_confirmed_history_before_save is True
    assert pipeline_input.clear_continuity_before_save is True


def test_director_confirmation_node_reuses_cached_executions():
    cached_decision = object()
    cached_executions = object()
    result = director_agent_confirm_node(
        {
            "write_db": True,
            "pipeline_input": SimpleNamespace(),
            "cache": {
                "director_decision": cached_decision,
                "director_executions": cached_executions,
            },
        }
    )

    assert result["director_decision"] is cached_decision
    assert result["director_executions"] is cached_executions


def test_director_confirmation_node_never_mutates_dry_run():
    result = director_agent_confirm_node(
        {
            "write_db": False,
            "pipeline_input": SimpleNamespace(),
            "cache": {},
        }
    )

    assert result == {
        "director_decision": None,
        "director_executions": None,
    }


def _prepare_persistence_test(monkeypatch, *, opening=False):
    from app.domain.core_memory import CoreMemory
    from app.domain.continuity import ContinuityProjection
    from app.domain.current_state.changes import StateChangeSet
    from app.domain.narrative_composer.schemas import NarrativeComposerContext
    from app.domain.plot_outline.schemas import PlotOutlineResult
    from app.domain.scene_summary.schemas import SceneSummary
    from app.graph.confirmation_lifecycle import ConfirmationMaintenanceScheduler

    runtime = get_campaign_runtime()
    if opening:
        config, record = _opening_initialization(experience_mode="player")
        pending = PendingOpening(
            turn_id="opening-save", group_id="opening-group", version_index=1,
            initialization_fingerprint=record.fingerprint, config=config,
            experience_mode="player", current_state=_state(),
            scene_result=SceneWriterResult(scene_text="开场正文"),
        )
        runtime.pending_openings.add(pending)
    else:
        with runtime.uow() as uow:
            uow.core_memory.save(CoreMemory(items=[]))
            uow.current_state.save(_state())
        pending = make_pending_turn(
            turn_id="turn-save", group_id="turn-group",
            user_input="观察四周", difficulty="困难", experience_mode="player",
            state_before=_state(),
            outline=PlotOutlineResult(scene_stopping_point="抵达北门"),
            scene_result=SceneWriterResult(scene_text="原始正文"),
        )
        runtime.pending_turns.add(pending)

    def computed_wave(_graph, state):
        # Replay computed domain outputs; persistence and finalization run unchanged.
        state["updated_state"] = _state().model_copy(update={
            "scene": _state().scene.model_copy(update={"location": "北门"}),
        })
        state["state_change_set"] = StateChangeSet(decision="记录最终场景")
        state["turn_summary"] = SceneSummary(
            content="抵达北门", level=1, scene_ids=["scene_001"],
            turn_start=state["pipeline_input"].turn_index,
            turn_end=state["pipeline_input"].turn_index,
        )
        state["continuity_projection"] = ContinuityProjection()
        state["cache"]["npc_turn_result"] = SimpleNamespace(
            process_result=SimpleNamespace(accepted_changes=[]),
        )
        return iter(())

    monkeypatch.setattr(confirmation_graph, "stream_confirmation_graph", computed_wave)
    monkeypatch.setattr(ConfirmationMaintenanceScheduler, "schedule_scene_summary", lambda *_: None)
    return runtime, pending


def test_selected_edited_variant_survives_undo_redo_and_reconfirmation(campaign_runtime, monkeypatch):
    from dataclasses import replace
    from app.core.client_error import ClientValidationError
    from app.runtime.player_state import load_confirmation_outcome
    from app.runtime import turn_history

    runtime, pending = _prepare_persistence_test(monkeypatch)
    alternative = replace(pending, turn_id="alternative", version_index=2, confirmation_cache={})
    runtime.pending_turns.add(alternative)
    list(confirmation_graph.confirm_stream(pending.turn_id, scene_text="玩家编辑的最终正文"))
    saved = runtime.history.get_transaction(pending.turn_id)
    assert saved["status"] == "completed"
    assert saved["scene_text"] == "玩家编辑的最终正文"
    with runtime.uow() as uow:
        assert uow.current_state.load().scene.location == "北门"
        assert [d.text for d in uow.confirmed_history.documents_for_turn(1)
                if d.source_type == "scene_paragraph"] == ["玩家编辑的最终正文"]
    with pytest.raises(ClientValidationError, match="已确认"):
        runtime.begin_confirmation(pending, write_db=True, scene_text=None)
    install = turn_history.install_snapshot

    def interrupt(*args):
        install(*args)
        raise OSError("interrupted installation")

    with monkeypatch.context() as patch:
        patch.setattr(turn_history, "install_snapshot", interrupt)
        with pytest.raises(OSError, match="interrupted installation"):
            runtime.undo()
    assert runtime.undo()["turn_id"] == pending.turn_id
    with runtime.uow() as uow:
        assert uow.current_state.load() == _state()
    assert {p.turn_id for p in runtime.pending_turns.values()} == {pending.turn_id, alternative.turn_id}
    assert runtime.history.get_transaction(pending.turn_id)["status"] == "awaiting_confirm"
    runtime.redo()
    assert not runtime.pending_turns.values()
    assert runtime.history.get_transaction(pending.turn_id)["status"] == "completed"
    runtime.undo()
    list(confirmation_graph.confirm_stream(alternative.turn_id, scene_text="改选版本的正文"))
    assert runtime.history.status()["can_redo"] is False
    assert load_confirmation_outcome(runtime.campaign_id, alternative.turn_id)["outcome"] == "confirmed"
    assert load_confirmation_outcome(runtime.campaign_id, pending.turn_id)["outcome"] != "confirmed"


def test_confirmation_failure_rolls_back_fact_and_domain_writes_then_retries(campaign_runtime, monkeypatch):
    from app.domain.world_setting import WorldSetting
    from app.graph.confirmation_nodes import setting_appearance_update_node

    runtime, pending = _prepare_persistence_test(monkeypatch)
    setting = WorldSetting(
        kind="location", name="北门", summary="城镇北侧的入口。",
        public_facts=["白天向行人开放。"], created_turn=0,
    )
    with runtime.uow(write=True) as uow:
        uow.world_setting.save(setting)
    scene_text = "玩家走到了北门。"
    computed_wave = confirmation_graph.stream_confirmation_graph

    def wave_with_setting_write(graph, state):
        yield from computed_wave(graph, state)
        yield from confirmation_graph.merge_confirmation_node_update(
            state, setting_appearance_update_node(state),
        )

    monkeypatch.setattr(confirmation_graph, "stream_confirmation_graph", wave_with_setting_write)
    original = runtime.history.record_confirmation

    def fail_after_record(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected write failure")

    monkeypatch.setattr(runtime.history, "record_confirmation", fail_after_record)
    with pytest.raises(RuntimeError, match="injected write failure"):
        list(confirmation_graph.confirm_stream(pending.turn_id, scene_text=scene_text))
    with runtime.uow() as uow:
        assert uow.current_state.load() == _state()
        assert uow.confirmed_history.turn_count() == 0
        assert uow.world_setting.get_by_id(setting.setting_id).hit_count == 0
    assert runtime.history.get_transaction(pending.turn_id)["status"] == "failed"
    assert runtime.pending_turns.get(pending.turn_id) is pending
    monkeypatch.setattr(runtime.history, "record_confirmation", original)
    list(confirmation_graph.confirm_stream(pending.turn_id, scene_text=scene_text))
    assert runtime.history.get_transaction(pending.turn_id)["scene_text"] == scene_text
    with runtime.uow() as uow:
        assert uow.world_setting.get_by_id(setting.setting_id).hit_count == 1


def test_saved_opening_survives_followup_failure_and_restart(campaign_runtime, monkeypatch, tmp_path):
    from app.runtime.session import CampaignRuntime
    from app.runtime.player_state import load_confirmation_outcome
    from app.runtime.paths import CampaignPaths

    get_campaign_runtime().reconfigure("pytest", CampaignPaths.from_data_dir(tmp_path / "user#%20name"))
    entries = set(tmp_path.iterdir())
    runtime, pending = _prepare_persistence_test(monkeypatch, opening=True)

    def fail_cleanup(*_):
        raise RuntimeError("undo publication failed")

    monkeypatch.setattr(runtime, "complete_confirmation", fail_cleanup)
    with pytest.raises(RuntimeError, match="undo publication failed"):
        list(confirmation_graph.confirm_stream(pending.turn_id))
    assert load_confirmation_outcome(runtime.campaign_id, pending.turn_id)["outcome"] == "confirmed"
    assert runtime.initialization.load().phase == "completed"
    assert not runtime.pending_openings.values()
    assert runtime.history.status()["can_undo"] is True
    runtime.close()
    reopened = CampaignRuntime("reopened", paths=runtime.paths)
    try:
        assert reopened.initialization.load().phase == "completed"
        assert reopened.history.pending_records() == []
        assert reopened.history.status()["can_undo"] is True
        reopened.undo()
        assert reopened.initialization.load().phase == "opening_pending"
        assert reopened.pending_openings.get(pending.turn_id) is not None
        reopened.redo()
        assert reopened.initialization.load().phase == "completed"
        assert not reopened.pending_openings.values()
        assert set(tmp_path.iterdir()) == entries
    finally:
        reopened.close()


def test_control_cleanup_failure_invalidates_saved_generation_cache(campaign_runtime, monkeypatch):
    from dataclasses import replace
    from app.runtime.player_state import load_confirmation_outcome

    runtime, pending = _prepare_persistence_test(monkeypatch)
    list(confirmation_graph.confirm_stream(pending.turn_id))
    first = pending
    with runtime.uow() as uow:
        current = uow.current_state.load()
    pending = replace(first, turn_id="second-turn", group_id="second-group",
                      state_before=current, confirmation_cache={})
    runtime.pending_turns.add(pending)
    epoch = runtime.generation_cache_epoch

    def fail_control_write(*_args):
        raise OSError("control write failed")

    with monkeypatch.context() as patch:
        patch.setattr(type(runtime.history), "complete_confirmation", fail_control_write)
        with pytest.raises(OSError, match="control write failed"):
            list(confirmation_graph.confirm_stream(pending.turn_id))
        assert runtime.generation_cache_epoch != epoch
        assert not runtime.pending_turns.values()
        for _ in range(2):
            with pytest.raises(OSError, match="control write failed"):
                runtime.undo()
            assert load_confirmation_outcome(runtime.campaign_id, first.turn_id)["outcome"] == "confirmed"
            assert load_confirmation_outcome(runtime.campaign_id, pending.turn_id)["outcome"] == "confirmed"
    assert runtime.undo()["turn_id"] == pending.turn_id
    assert load_confirmation_outcome(runtime.campaign_id, first.turn_id)["outcome"] == "confirmed"
    assert load_confirmation_outcome(runtime.campaign_id, pending.turn_id)["outcome"] == "not_confirmed"
    runtime.redo()
    assert load_confirmation_outcome(runtime.campaign_id, pending.turn_id)["outcome"] == "confirmed"
