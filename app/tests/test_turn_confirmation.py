from types import SimpleNamespace

from app.domain.core_memory import CampaignSetupProposal, InitConfig
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.opening_foundation import OpeningFoundation
from app.domain.scene_writer import SceneWriterResult
from app.graph import confirmation_graph
from app.graph.confirmation_nodes import director_agent_confirm_node
from app.runtime.initialization import commit_campaign_setup
from app.runtime.session import PendingOpening, PendingTurn, get_campaign_runtime


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


def test_turn_confirmation_runs_director_pipeline_for_player_and_creator(monkeypatch):
    runtime = get_campaign_runtime()
    captured_modes = []

    def capture(pipeline_input, **_kwargs):
        captured_modes.append(pipeline_input.pending.experience_mode)
        return iter(())

    monkeypatch.setattr(confirmation_graph, "_confirm_pipeline_locked", capture)
    for mode in ("player", "creator"):
        turn_id = f"turn-{mode}"
        runtime.pending_turns.add(
            PendingTurn(
                turn_id=turn_id,
                group_id=turn_id,
                version_index=1,
                user_input="观察四周",
                difficulty="困难",
                max_chars=1500,
                experience_mode=mode,
                state_before=_state(),
                context=SimpleNamespace(action_judgments=[]),
                outline=SimpleNamespace(),
                scene_result=SceneWriterResult(scene_text="场景正文"),
            )
        )
        try:
            list(confirmation_graph._confirm_turn_stream_locked(turn_id))
        finally:
            runtime.pending_turns.remove(turn_id)

    # Both modes reach the full confirmation pipeline; Director always runs.
    assert captured_modes == ["player", "creator"]


def test_opening_confirmation_always_runs_director_and_reset_flags(monkeypatch):
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
