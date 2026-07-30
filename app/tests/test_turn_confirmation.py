from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.domain.core_memory import CampaignSetupProposal, CoreMemory, InitConfig
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.entity.schemas import EntityExtractionResult
from app.domain.director_event.workflows import DirectorEventWorkflowResult
from app.domain.npc_profile.schemas import NPCExtractionResult
from app.domain.opening_foundation import OpeningFoundation
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory import ShortTermMemory
from app.runtime.session import (
    PendingOpening,
    PendingTurn,
    get_campaign_runtime,
)
from app.runtime.initialization import commit_campaign_setup
from app.graph import confirmation_graph as turn_confirmation
from app.graph import confirmation_helpers
from app.graph import confirmation_nodes


class _ContextStore:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def close(self):
        return None


class _ActionPreviewAgent:
    def __init__(self, _gateway):
        pass

    def generate(self, **_kwargs):
        return {"previews": []}


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="测试地点",
            participants=["主角"],
        ),
        player=PlayerState(name="主角"),
    )


def _prepare_opening_initialization(*, director_enabled=True):
    runtime = get_campaign_runtime()
    config = InitConfig(
        world_name="测试世界",
        world_style="奇幻",
        ability_system="魔法",
        protagonist_name="主角",
        story_expectation="探索并逐步成长。",
        director_enabled=director_enabled,
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
    foundation = OpeningFoundation.model_validate({
        "starting_location": {
            "name": "测试地点",
            "description": "用于测试的地点。",
        },
        "protagonist_opening_situation": "主角已抵达测试地点，需要决定今日去向。",
        "initial_resources": {
            "inventory": [],
            "money": 5,
            "fixed_assets": [],
        },
    })
    record = record.model_copy(update={
        "phase": "opening_pending",
        "foundation": foundation,
    })
    runtime.initialization.save(record)
    with runtime.stores.current_state() as state_store:
        state_store.save(_state())
    return config, record


def test_turn_confirmation_skips_all_director_work_when_disabled(monkeypatch):
    runtime = get_campaign_runtime()
    captured = {}
    turn_id = "director-disabled-turn"
    state = _state()
    runtime.pending_turns.add(PendingTurn(
        turn_id=turn_id,
        group_id=turn_id,
        version_index=1,
        user_input="观察四周",
        difficulty="困难",
        max_chars=1500,
        state_before=state,
        context=SimpleNamespace(turn_resolution=SimpleNamespace()),
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="场景正文"),
    ))
    def _capture(pipeline_input, **_kwargs):
        captured["input"] = pipeline_input
        return iter(())

    monkeypatch.setattr(turn_confirmation, "director_enabled", lambda: False)
    monkeypatch.setattr(
        turn_confirmation,
        "_confirm_pipeline_locked",
        _capture,
    )
    try:
        list(turn_confirmation._confirm_turn_stream_locked(turn_id))
    finally:
        runtime.pending_turns.remove(turn_id)

    assert captured["input"].update_director_event is False
    assert captured["input"].handle_director_npc is False


def test_opening_confirmation_honors_initial_director_choice(monkeypatch):
    runtime = get_campaign_runtime()
    turn_id = "director-disabled-opening"
    config, record = _prepare_opening_initialization(director_enabled=False)
    runtime.pending_openings.add(PendingOpening(
        turn_id=turn_id,
        group_id=turn_id,
        version_index=1,
        initialization_fingerprint=record.fingerprint,
        config=config,
        current_state=_state(),
        scene_result=SceneWriterResult(scene_text="开场正文"),
    ))
    captured = {}

    def _capture(pipeline_input, **_kwargs):
        captured["input"] = pipeline_input
        return iter(())

    monkeypatch.setattr(
        turn_confirmation,
        "_confirm_pipeline_locked",
        _capture,
    )
    try:
        list(turn_confirmation._confirm_opening_stream_locked(turn_id))
    finally:
        runtime.pending_openings.remove(turn_id)

    assert captured["input"].handle_director_npc is False
    assert captured["input"].director_npc_restock_sync is False
    assert captured["input"].clear_short_term_before_save is True
    assert captured["input"].clear_confirmed_history_before_save is True


def test_failed_preview_retries_only_preview_and_dry_run_keeps_pending(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "retryable-turn"
    state = _state()
    pending = PendingTurn(
        turn_id=turn_id,
        group_id=turn_id,
        version_index=1,
        user_input="观察四周",
        difficulty="困难",
        max_chars=1500,
        state_before=state,
        context=SimpleNamespace(turn_resolution=SimpleNamespace()),
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="场景正文"),
        turn_index=1,
        short_term_memory=ShortTermMemory(),
    )
    pending_turns = get_campaign_runtime().pending_turns
    pending_turns.add(pending)
    calls = {"state": 0, "preview": 0}

    class _StateUpdater:
        def __init__(self, _gateway):
            pass

        def update(self, current_state, *_args, **_kwargs):
            calls["state"] += 1
            return current_state

    def _preview(**_kwargs):
        calls["preview"] += 1
        if calls["preview"] == 1:
            raise RuntimeError("preview failed")
        return {"previews": ["ok"]}

    class _RetryingActionPreviewAgent:
        def __init__(self, _gateway):
            pass

        def generate(self, **kwargs):
            return _preview(**kwargs)

    class _FakeGateway:
        def set_trace_context(self, **kwargs):
            pass

        def run(self, **kwargs):
            if kwargs.get("task") == "scene_fact_extract":
                return kwargs["output_schema"].model_validate(
                    {"scene_focus": "测试场景事实", "facts": []}
                )
            if kwargs.get("task") == "scene_summarize":
                return kwargs["output_schema"].model_validate({"content": "本轮摘要"})
            return kwargs["output_schema"].model_validate({})

    configure_runtime_services(
        gateway_factory=_FakeGateway,
        core_memory=lambda: _ContextStore(
            load_required=lambda: CoreMemory(items=[])
        ),
        scene_summary=lambda: _ContextStore(
            list_active=lambda: [],
            get_recent=lambda _limit=5: [],
        ),
        short_term=lambda: _ContextStore(get_recent=lambda: ShortTermMemory()),
        continuity=lambda: _ContextStore(count=lambda: 0, list_active=lambda: []),
    )
    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _StateUpdater)
    monkeypatch.setattr(
        confirmation_nodes,
        "ActionPreviewAgent",
        _RetryingActionPreviewAgent,
    )

    try:
        with pytest.raises(RuntimeError, match="preview failed"):
            list(
                turn_confirmation._confirm_turn_stream_locked(
                    turn_id,
                    write_db=False,
                )
            )

        events = list(
            turn_confirmation._confirm_turn_stream_locked(
                turn_id,
                write_db=False,
            )
        )

        assert calls == {"state": 1, "preview": 2}
        assert events[-1]["data"]["status"] == "dry_run"
        assert turn_id in pending_turns
    finally:
        pending_turns.remove(turn_id)


def test_confirmation_projects_continuity_without_waiting_for_summary(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "summary-future-turn"
    state = _state()
    summary_future = Future()
    summary_future.set_result(None)
    pending = PendingTurn(
        turn_id=turn_id,
        group_id=turn_id,
        version_index=1,
        user_input="继续观察",
        difficulty="困难",
        max_chars=1500,
        state_before=state,
        context=SimpleNamespace(turn_resolution=SimpleNamespace()),
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="场景正文"),
        turn_index=14,
        short_term_memory=ShortTermMemory(),
        confirmation_cache={
            "key": "场景正文",
            "scene_summary_scheduled": True,
            "scene_summary_future": summary_future,
        },
    )
    pending_turns = get_campaign_runtime().pending_turns
    pending_turns.add(pending)
    sibling_turn_id = "summary-future-turn-v2"
    pending_turns.add(replace(
        pending,
        turn_id=sibling_turn_id,
        version_index=2,
        confirmation_cache={},
    ))
    captured = {}

    class _FakeGateway:
        def set_trace_context(self, **_kwargs):
            pass

        def run(self, **kwargs):
            if kwargs.get("task") == "scene_fact_extract":
                return kwargs["output_schema"].model_validate(
                    {"scene_focus": "测试场景事实", "facts": []}
                )
            if kwargs.get("task") == "scene_summarize":
                return kwargs["output_schema"].model_validate({"content": "本轮摘要"})
            return kwargs["output_schema"].model_validate({})

    class _StateUpdater:
        def __init__(self, _gateway):
            pass

        def update(self, current_state, *_args, **_kwargs):
            return current_state

    class _EntityExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, **_kwargs):
            return EntityExtractionResult()

    class _NPCExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, **_kwargs):
            return NPCExtractionResult()

    runtime = get_campaign_runtime()
    configure_runtime_services(
        gateway_factory=_FakeGateway,
        core_memory=lambda: _ContextStore(
            load_required=lambda: CoreMemory(items=[])
        ),
        current_state=lambda: _ContextStore(
            load_or_default=lambda: state,
            save=lambda _state: None,
        ),
        scene_summary=lambda: _ContextStore(
            list_active=lambda: [],
            get_recent=lambda _limit=5: [],
            save_one=lambda summary: summary.id,
        ),
        short_term=lambda: _ContextStore(
            add_turn=lambda **_kwargs: None,
            get_recent=lambda: ShortTermMemory(),
        ),
        entity=lambda: _ContextStore(list_all=lambda: []),
        npc_profile=lambda: _ContextStore(list_all=lambda: []),
        director_event=lambda: _ContextStore(list_active=lambda: []),
        director_npc=lambda: _ContextStore(
            list_standby=lambda: [object()]
            * confirmation_helpers.DIRECTOR_NPC_STANDBY_TARGET
        ),
    )
    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _StateUpdater)
    monkeypatch.setattr(confirmation_nodes, "EntityExtractor", _EntityExtractor)
    monkeypatch.setattr(confirmation_nodes, "NPCProfileExtractor", _NPCExtractor)
    monkeypatch.setattr(
        confirmation_nodes,
        "ActionPreviewAgent",
        _ActionPreviewAgent,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_director_event_workflow",
        lambda **kwargs: DirectorEventWorkflowResult(
            role=kwargs["role"],
            mode="test",
        ),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_entity_profiles_from_extraction",
        lambda **_kwargs: SimpleNamespace(saved_ids=[]),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_npc_profiles_from_extraction",
        lambda **_kwargs: SimpleNamespace(saved_ids=[]),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "sync_npc_profile_absence_from_current_state",
        lambda **_kwargs: [],
    )

    try:
        events = list(
            turn_confirmation._confirm_turn_stream_locked(
                turn_id,
                write_db=True,
            )
        )
        runtime.jobs.wait_for_idle()
        group_removed = (
            turn_id not in pending_turns
            and sibling_turn_id not in pending_turns
        )
        with runtime.services.stores.confirmed_history() as history_store:
            confirmed_documents = history_store.list_documents()
    finally:
        pending_turns.remove(turn_id)
        pending_turns.remove(sibling_turn_id)

    assert events[-1]["data"]["status"] == "committed"
    assert any(event["step"] == "continuity_projected" for event in events)
    assert group_removed is True
    assert confirmed_documents
    assert [document.text for document in confirmed_documents] == [
        "继续观察",
        "场景正文",
    ]


def test_opening_confirmation_restocks_director_npc_synchronously(
    monkeypatch,
    configure_runtime_services,
):
    turn_id = "opening-director-npc"
    state = _state()
    config, record = _prepare_opening_initialization()
    pending = PendingOpening(
        turn_id=turn_id,
        group_id=turn_id,
        version_index=1,
        initialization_fingerprint=record.fingerprint,
        config=config,
        current_state=state,
        scene_result=SceneWriterResult(scene_text="开场正文"),
    )
    pending_openings = get_campaign_runtime().pending_openings
    pending_openings.add(pending)
    pending_openings.add(PendingOpening(
        turn_id="opening-director-npc-v2",
        group_id=turn_id,
        version_index=2,
        initialization_fingerprint=record.fingerprint,
        config=config,
        current_state=state,
        scene_result=SceneWriterResult(scene_text="另一版开场正文"),
    ))
    calls = {"restock": 0}

    class _FakeGateway:
        def set_trace_context(self, **_kwargs):
            pass

        def run(self, **kwargs):
            if kwargs.get("task") == "scene_fact_extract":
                return kwargs["output_schema"].model_validate(
                    {"scene_focus": "测试场景事实", "facts": []}
                )
            if kwargs.get("task") == "scene_summarize":
                return kwargs["output_schema"].model_validate({"content": "本轮摘要"})
            return kwargs["output_schema"].model_validate({})

    class _StateUpdater:
        def __init__(self, _gateway):
            pass

        def update(self, current_state, *_args, **_kwargs):
            return current_state

    class _EntityExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, **_kwargs):
            return EntityExtractionResult()

    class _NPCExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, **_kwargs):
            return NPCExtractionResult()

    configure_runtime_services(
        gateway_factory=_FakeGateway,
        core_memory=lambda: _ContextStore(
            load_required=lambda: CoreMemory(items=[])
        ),
        current_state=lambda: _ContextStore(
            load_or_default=lambda: state,
            save=lambda _state: None,
        ),
        scene_summary=lambda: _ContextStore(
            list_active=lambda: [],
            get_recent=lambda _limit=5: [],
            save_one=lambda summary: summary.id,
        ),
        short_term=lambda: _ContextStore(
            clear=lambda: None,
            add_turn=lambda **_kwargs: None,
            get_recent=lambda: ShortTermMemory(),
        ),
        entity=lambda: _ContextStore(list_all=lambda: []),
        npc_profile=lambda: _ContextStore(list_all=lambda: []),
        director_npc=lambda: _ContextStore(list_standby=lambda: []),
    )
    with get_campaign_runtime().services.stores.confirmed_history() as history_store:
        history_store.upsert_turn(
            scene_id="obsolete_scene",
            turn_index=99,
            user_input="旧玩家输入",
            scene_text="不应保留的旧正文。",
        )
    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _StateUpdater)
    monkeypatch.setattr(confirmation_nodes, "EntityExtractor", _EntityExtractor)
    monkeypatch.setattr(confirmation_nodes, "NPCProfileExtractor", _NPCExtractor)
    monkeypatch.setattr(
        confirmation_nodes,
        "ActionPreviewAgent",
        _ActionPreviewAgent,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_entity_profiles_from_extraction",
        lambda **_kwargs: SimpleNamespace(saved_ids=[]),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_npc_profiles_from_extraction",
        lambda **_kwargs: SimpleNamespace(saved_ids=[]),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "sync_npc_profile_absence_from_current_state",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "run_scene_summary_maintenance",
        lambda *_args, **_kwargs: None,
    )
    def _restock(*_args, **_kwargs):
        calls["restock"] += 1

    monkeypatch.setattr(
        confirmation_helpers,
        "run_director_npc_maintenance",
        _restock,
    )

    try:
        events = list(
            turn_confirmation._confirm_opening_stream_locked(
                turn_id,
                write_db=True,
            )
        )
        with (
            get_campaign_runtime().services.stores.confirmed_history()
            as history_store
        ):
            confirmed_documents = history_store.list_documents()
        assert pending_openings.variants(turn_id) == []
    finally:
        pending_openings.remove(turn_id)

    assert calls["restock"] == 1
    assert events[-1]["data"]["status"] == "committed"
    assert events[-1]["data"]["director_npc_restock_scheduled"] is True
    assert get_campaign_runtime().initialization.load().phase == "completed"
    assert [
        (document.source_type, document.turn_index, document.text)
        for document in confirmed_documents
    ] == [("scene_paragraph", 0, "开场正文")]
