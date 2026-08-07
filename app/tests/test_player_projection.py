import pytest

from types import SimpleNamespace
from app.debug_server import DebugHandler
from app.domain.core_memory import CampaignSetupProposal
from app.domain.core_memory import CoreMemory
from app.domain.core_memory import CoreMemoryItem
from app.domain.core_memory import InitConfig
from app.domain.current_state import CurrentState
from app.domain.current_state import PlayerState
from app.domain.current_state import ResourcesState
from app.domain.current_state import SceneState
from app.domain.current_state import WorldClock
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.world_setting.schemas import WorldSetting
from app.runtime.player_state import load_player_history
from app.runtime.player_state import load_player_state
from app.runtime.player_state import player_saves_payload
from app.runtime.initialization import InitializationRecord
from app.runtime.session import get_campaign_runtime

from app.tests.factories.domains import (
    make_action_preview,
    make_player_resolution,
)


def test_player_state_is_allow_listed_and_restores_pending_content():
    runtime = get_campaign_runtime()
    core_memory = CoreMemory(items=[
        CoreMemoryItem(
            id="secret",
            kind="world_invariant",
            strength="hard_constraint",
            source="player",
            content="不可泄露",
        )
    ])
    runtime.stores.core_memory().save(core_memory)
    runtime.initialization.save(InitializationRecord(
        phase="completed",
        fingerprint="test-fingerprint",
        config=InitConfig(
            world_name="测试世界",
            world_style="奇幻",
            ability_system="魔法",
            protagonist_name="莱尔",
            story_expectation="探索世界。",
        ),
        campaign_setup=CampaignSetupProposal(
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
        ),
        core_memory=core_memory,
    ))
    with runtime.stores.current_state() as store:
        store.save(CurrentState(
            scene=SceneState(
                scene_id="scene_7",
                location="老橡树镇",
                participants=[
                    "莱尔",
                    {
                        "name": "埃利奥",
                        "attitude_to_player": "表面友好，实际警惕",
                    },
                ],
            ),
            world_clock=WorldClock(day=4, phase="晚上"),
            player=PlayerState(name="莱尔"),
            resources=ResourcesState(money=17, money_unit="金币"),
        ))
    with runtime.stores.short_term() as store:
        store.add_turn(
            "scene_7",
            6,
            "询问木偶的事。",
            "莱尔在图书馆找到了木偶师埃利奥。",
            "继续追问吗？",
            action_preview=make_action_preview(),
            player_resolution=make_player_resolution(),
            token_usage={
                "generation": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                },
                "confirmation": {
                    "input_tokens": 40,
                    "output_tokens": 10,
                    "total_tokens": 50,
                },
            },
        )
    with runtime.stores.npc_profile() as store:
        store.upsert(NPCProfile(
            name="埃利奥",
            aliases=["木偶师", "莱尔"],
            role="流浪木偶师",
            public_summary="一名公开身份不明的木偶师。",
            hidden_secret="不应投影的 NPC 秘密",
        ))
    with runtime.stores.world_setting() as store:
        store.save(WorldSetting(
            kind="location",
            name="图书馆",
            summary="一座石砌的公共图书馆。",
            public_facts=["登记读者可借阅。"],
            hidden_facts=["地下层保存未登记档案。"],
            created_turn=1,
        ))
    runtime.pending_turns.add(SimpleNamespace(
        turn_id="turn-v1",
        group_id="group-1",
        version_index=1,
        turn_index=7,
        user_input="继续追问。",
        difficulty="普通",
        scene_result=SimpleNamespace(
            scene_text="埃利奥终于开口。",
            closing_prompt="你准备如何回应？",
        ),
        token_usage={
            "generation": {
                "input_tokens": 90,
                "output_tokens": 20,
                "total_tokens": 110,
            },
            "confirmation": None,
        },
    ))

    payload = load_player_state()

    assert set(payload) == {
        "save", "needs_init", "difficulty", "campaign", "history",
        "history_navigation", "history_pagination", "highlights", "pending",
        "history_controls", "action_preview", "dice_display",
        "canon_validation_enabled", "director_enabled",
        "initialization",
    }
    assert payload["campaign"]["participants"] == ["莱尔", "埃利奥"]
    assert "scene_conditions" not in payload["campaign"]
    assert "conditions" not in payload["campaign"]["player"]
    assert "attitude_to_player" not in str(payload)
    assert "暗中恐惧" not in str(payload)
    assert "不可泄露" not in str(payload)
    assert "流浪木偶师" in str(payload)
    assert "不应投影的 NPC 秘密" not in str(payload)
    assert {item["surface"] for item in payload["highlights"]} == {
        "木偶师", "埃利奥", "图书馆",
    }
    assert {item["kind"] for item in payload["highlights"]} == {"npc", "setting"}
    setting_highlight = next(
        item for item in payload["highlights"] if item["kind"] == "setting"
    )
    assert setting_highlight["profile"]["setting_kind"] == "location"
    assert "地下层保存未登记档案。" not in str(payload)
    assert not any(
        item["surface"] == "莱尔"
        for item in payload["highlights"]
    )
    assert payload["pending"][0]["variants"][0]["closing_prompt"] == "你准备如何回应？"
    assert payload["pending"][0]["variants"][0]["token_usage"]["generation"][
        "total_tokens"
    ] == 110
    assert payload["action_preview"] == make_action_preview().model_dump(mode="json")
    assert payload["history"][0]["player_resolution"] == make_player_resolution().model_dump(mode="json")
    assert payload["history"][0]["token_usage"]["generation"]["total_tokens"] == 150
    assert payload["history"][0]["token_usage"]["confirmation"]["total_tokens"] == 50
    assert payload["history_navigation"] == [{
        "turn_index": 6,
        "user_input_excerpt": "询问木偶的事。",
    }]
    assert payload["dice_display"] == "outcome"
    assert payload["canon_validation_enabled"] is False
    assert payload["director_enabled"] is True


def test_text_highlights_exclude_single_char_settings_and_prefer_npc_on_collision():
    from app.runtime.player_state import _project_text_highlights

    runtime = get_campaign_runtime()
    with runtime.stores.npc_profile() as store:
        store.upsert(NPCProfile(
            name="商会",
            role="行商首领",
            public_summary="执掌商会的老人。",
        ))
    with runtime.stores.world_setting() as store:
        store.save(WorldSetting(
            kind="lore",
            name="道",
            summary="修行之途。",
            public_facts=["道法自然。"],
            created_turn=1,
        ))
        store.save(WorldSetting(
            kind="faction",
            name="商会",
            summary="北境的商帮。",
            public_facts=["收取商税。"],
            created_turn=1,
        ))

    items = _project_text_highlights(
        runtime,
        texts=["他知晓商会收税的规矩，也听闻道法自然的道理。"],
        player_name="莱尔",
    )

    surfaces = {item["surface"]: item for item in items}
    assert "道" not in surfaces
    assert surfaces["商会"]["kind"] == "npc"
    assert not any(
        item["kind"] == "setting"
        for item in items
        if item["surface"] == "商会"
    )

    again = _project_text_highlights(
        runtime,
        texts=["他知晓商会收税的规矩，也听闻道法自然的道理。"],
        player_name="莱尔",
    )
    assert [item["surface"] for item in again] == [item["surface"] for item in items]


def test_player_history_loads_five_turn_pages_oldest_to_newest():
    runtime = get_campaign_runtime()
    with runtime.stores.short_term() as store:
        for turn_index in range(1, 13):
            store.add_turn(
                f"scene-{turn_index}",
                turn_index,
                f"行动 {turn_index}",
                f"正文 {turn_index}",
            )

    initial = load_player_state()
    assert [turn["turn_index"] for turn in initial["history"]] == [8, 9, 10, 11, 12]
    assert [item["turn_index"] for item in initial["history_navigation"]] == list(range(1, 13))
    assert initial["history_pagination"]["has_more"] is True

    previous = load_player_history(before_turn_index=8, limit=5)
    assert [turn["turn_index"] for turn in previous["history"]] == [3, 4, 5, 6, 7]
    assert previous["pagination"]["has_more"] is True

    oldest = load_player_history(before_turn_index=3, limit=5)
    assert [turn["turn_index"] for turn in oldest["history"]] == [1, 2]
    assert oldest["pagination"]["has_more"] is False


def test_player_history_navigation_normalizes_and_truncates_user_input():
    runtime = get_campaign_runtime()
    with runtime.stores.short_term() as store:
        store.add_turn("opening", 0, "", "开场正文")
        store.add_turn(
            "scene-1",
            1,
            "  这是一个包含换行\n和   多余空白、并且长度明显超过导航摘要上限的玩家行动输入  ",
            "第一回合正文",
        )

    navigation = load_player_state()["history_navigation"]

    assert navigation == [
        {"turn_index": 0, "user_input_excerpt": ""},
        {
            "turn_index": 1,
            "user_input_excerpt": "这是一个包含换行 和 多余空白、并且长度明显超过导航摘要上限的…",
        },
    ]
    assert len(navigation[1]["user_input_excerpt"]) == 32


def test_player_history_navigation_preserves_sparse_turn_indexes():
    runtime = get_campaign_runtime()
    with runtime.stores.short_term() as store:
        store.add_turn("scene-2", 2, "第二回合行动", "第二回合正文")
        store.add_turn("scene-7", 7, "第七回合行动", "第七回合正文")

    assert load_player_state()["history_navigation"] == [
        {"turn_index": 2, "user_input_excerpt": "第二回合行动"},
        {"turn_index": 7, "user_input_excerpt": "第七回合行动"},
    ]


def test_player_history_navigation_is_empty_without_confirmed_turns():
    assert load_player_state()["history_navigation"] == []


def test_player_event_projection_removes_internal_generation_data():
    event = DebugHandler._player_event({
        "step": "scene_written",
        "data": {
            "turn_id": "turn-1",
            "group_id": "group-1",
            "version_index": 2,
            "turn_index": 7,
            "trace_id": "private-trace",
            "context": {
                "director_stage": {
                    "reasoning": "secret",
                    "materials": [],
                }
            },
            "scene_result": {
                "scene_text": "公开正文",
                "closing_prompt": "接下来怎么办？",
                "next_action_directions": ["private"],
            },
        },
    })

    assert event == {
        "step": "scene_written",
        "data": {
            "turn_id": "turn-1",
            "group_id": "group-1",
            "version_index": 2,
            "turn_index": 7,
            "scene_text": "公开正文",
            "closing_prompt": "接下来怎么办？",
        },
    }
    assert "private" not in str(event)


def test_player_event_projection_preserves_revision_stream_marker():
    event = DebugHandler._player_event({
        "step": "scene_stream_started",
        "data": {
            "attempt": 2,
            "revision": True,
            "trace_id": "private-trace",
        },
    })

    assert event == {
        "step": "scene_stream_started",
        "data": {
            "attempt": 2,
            "version_index": None,
            "revision": True,
        },
    }


def test_player_event_projection_exposes_state_update_warnings():
    event = DebugHandler._player_event({
        "step": "finish",
        "data": {
            "turn_id": "turn-1",
            "status": "committed",
            "action_preview": {"previews": []},
            "state_update_warnings": [{
                "operation_id": "fallback",
                "operation_type": "state_update",
                "target": "",
                "code": "state_update_fallback",
                "message": "State update fell back to the previous state: bad",
            }],
            "state_before": {"private": True},
        },
    })

    assert event == {
        "step": "finish",
        "data": {
            "turn_id": "turn-1",
            "status": "committed",
            "action_preview": {"previews": []},
            "state_update_warnings": [{
                "operation_id": "fallback",
                "operation_type": "state_update",
                "target": "",
                "code": "state_update_fallback",
                "message": "State update fell back to the previous state: bad",
            }],
        },
    }
    assert "state_before" not in str(event)


def test_player_event_projection_exposes_only_safe_llm_usage_counts():
    event = DebugHandler._player_event({
        "step": "llm_usage",
        "data": {
            "task": "narrative_compose",
            "call_index": 2,
            "attempt": 1,
            "input_tokens": 1200,
            "output_tokens": 340,
            "total_tokens": 1540,
            "reported": True,
            "trace_id": "private-trace",
            "prompt": "private prompt",
            "raw_output": "private output",
        },
    })

    assert event == {
        "step": "llm_usage",
        "data": {
            "task": "narrative_compose",
            "call_index": 2,
            "attempt": 1,
            "input_tokens": 1200,
            "output_tokens": 340,
            "total_tokens": 1540,
            "reported": True,
        },
    }
    assert "private" not in str(event)


def test_player_campaign_setup_event_exposes_only_editable_result():
    event = DebugHandler._player_event({
        "step": "campaign_setup_done",
        "data": {
            "config": {"protagonist_name": "莱尔"},
            "campaign_setup": {
                "version": 2,
                "currency_unit": "银币",
                "public_power_baseline": {
                    "source": "魔法",
                    "prevalence": "少数人掌握",
                    "learning_difficulty": "困难",
                    "limitation": "不能复活死者。",
                },
                "peoples_and_beings": ["人类", "精灵"],
                "major_world_conflicts": ["帝国扩张持续挤压边境诸国。"],
                "dominant_powers": ["北方帝国", "圣辉教会"],
                "civilization_baseline": "以马车、冷兵器和手工业为主。",
            },
            "core_memory": {"items": [{"content": "internal"}]},
            "trace_id": "private-trace",
        },
    })

    assert event == {
        "step": "campaign_setup_done",
        "data": {
            "config": {"protagonist_name": "莱尔"},
            "campaign_setup": {
                "version": 2,
                "currency_unit": "银币",
                "public_power_baseline": {
                    "source": "魔法",
                    "prevalence": "少数人掌握",
                    "learning_difficulty": "困难",
                    "limitation": "不能复活死者。",
                },
                "peoples_and_beings": ["人类", "精灵"],
                "major_world_conflicts": ["帝国扩张持续挤压边境诸国。"],
                "dominant_powers": ["北方帝国", "圣辉教会"],
                "civilization_baseline": "以马车、冷兵器和手工业为主。",
            },
        },
    }


def test_player_operation_projection_is_allow_listed():
    operation = DebugHandler._player_operation({
        "operation_id": "operation-1",
        "operation_type": "generation",
        "status": "running",
        "current_step": "action_parsed",
        "submitted_at": "now",
        "started_at": "now",
        "result": {"private": "result"},
        "error_message": "private error",
        "metadata": {
            "user_input": "调查钟楼",
            "turn_id": "turn-1",
            "trace_id": "private-trace",
        },
    })

    assert operation == {
        "operation_id": "operation-1",
        "operation_type": "generation",
        "status": "running",
        "current_step": "action_parsed",
        "submitted_at": "now",
        "started_at": "now",
        "recovery_kind": "generation",
        "metadata": {
            "user_input": "调查钟楼",
            "turn_id": "turn-1",
        },
    }
    assert "private" not in str(operation)


def test_player_save_payload_removes_server_root():
    payload = player_saves_payload({
        "active": "8e4e1da7-00a0-4a63-83c7-d6726694077a",
        "root": "/private/server/path",
        "saves": [{
            "save_id": "8e4e1da7-00a0-4a63-83c7-d6726694077a",
            "name": "莱尔",
            "active": True,
            "initialized": True,
            "last_played_at": "now",
            "turn_count": 3,
            "protagonist_name": "莱尔",
            "location": "北门",
            "path": "/private/server/path/莱尔",
        }],
    })

    assert "root" not in payload
    assert "path" not in payload["saves"][0]
