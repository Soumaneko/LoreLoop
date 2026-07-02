import json

from app.domain.core_memory import CoreMemory
from app.domain.current_state.schemas import CurrentState, NPCState, PlayerState, SceneState
from app.domain.npc_profile.schemas import NPCExtractionResult, NPCProfile, RetrievedNPC
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.workflows import (
    sync_npc_profile_absence_from_current_state,
    update_npc_profiles_from_extraction,
)


class _Gateway:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, str):
            response = json.loads(response)
        return kwargs["output_schema"].model_validate(response)


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="醉花楼", participants=["萧云痕"]),
        player=PlayerState(name="萧云痕", position="醉花楼"),
    )


def test_sync_npc_profile_absence_resets_current_state_hits_and_increments_misses():
    store = NPCProfileStore(":memory:")
    present_id = store.upsert(
        NPCProfile(name="裴玉奴", aliases=["裴姑娘"], role="琵琶名手", absent_turns=2)
    )
    missing_id = store.upsert(
        NPCProfile(name="周泰", role="巡检司都头", absent_turns=1)
    )
    state = CurrentState(
        scene=SceneState(
            scene_id="scene_005",
            location="鸣玉坊里间",
            participants=["萧云痕", "裴姑娘"],
        ),
        player=PlayerState(name="萧云痕", position="鸣玉坊里间"),
        npcs=[NPCState(name="裴玉奴", role="琵琶名手")],
    )

    saved_ids = sync_npc_profile_absence_from_current_state(
        npc_store=store,
        current_state=state,
    )

    assert set(saved_ids) == {present_id, missing_id}
    assert store.get_by_id(present_id).absent_turns == 0
    assert store.get_by_id(missing_id).absent_turns == 2


def test_sync_npc_profile_absence_ignores_absent_current_state_npcs():
    store = NPCProfileStore(":memory:")
    absent_id = store.upsert(
        NPCProfile(name="裴玉奴", aliases=["裴姑娘"], role="琵琶名手", absent_turns=1)
    )
    present_id = store.upsert(
        NPCProfile(name="花想容", role="胭脂铺掌柜", absent_turns=2)
    )
    state = CurrentState(
        scene=SceneState(
            scene_id="scene_008",
            location="白鹤巷第三进院外墙下",
            participants=["萧云痕", "花想容"],
        ),
        player=PlayerState(name="萧云痕", position="白鹤巷第三进院外墙下"),
        npcs=[
            NPCState(name="裴玉奴", role="琵琶名手", absent_turns=1),
            NPCState(name="花想容", role="胭脂铺掌柜", absent_turns=0),
        ],
    )

    saved_ids = sync_npc_profile_absence_from_current_state(
        npc_store=store,
        current_state=state,
    )

    assert set(saved_ids) == {absent_id, present_id}
    assert store.get_by_id(absent_id).absent_turns == 2
    assert store.get_by_id(present_id).absent_turns == 0


def test_new_non_director_npc_triggers_profile_backfill():
    store = NPCProfileStore(":memory:")
    gateway = _Gateway(
        [
            {
                "npcs": [
                    {
                        "npc_index": 0,
                        "profile": {
                            "name": "柳莺莺",
                            "aliases": [],
                            "public_summary": "醉花楼花魁，琴艺出众。",
                            "role": "醉花楼花魁",
                            "appearance": "青衣抱琵琶，姿态温婉。",
                            "personality_traits": ["温婉", "谨慎"],
                            "known_abilities": ["琵琶"],
                            "faction": "醉花楼",
                            "description": None,
                            "current_location": "醉花楼",
                            "attitude_to_player": None,
                            "current_intent": "在大堂演奏",
                        },
                    }
                ]
            },
            {
                "name": "柳莺莺",
                "role": "醉花楼花魁",
                "appearance": "青衣抱琵琶，姿态温婉。",
                "personality_traits": ["温婉", "精明"],
                "known_abilities": ["琵琶", "察言观色"],
                "faction": "醉花楼",
                "core_motivation": "攒够赎身钱，拥有能自主选择客人与去处的生活。",
                "hidden_secret": "她暗中替城中几家商号传递消息，借此积累人脉。",
                "conflict_style": "先以言语周旋，必要时借掌柜和熟客施压。",
                "social_style": "温柔含蓄，习惯用曲意和笑语试探对方。",
            },
        ]
    )

    result = update_npc_profiles_from_extraction(
        scene_text="柳莺莺在醉花楼大堂弹琵琶。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[],
        extraction=NPCExtractionResult(
            retrieved_npcs=[
                RetrievedNPC(
                    name="柳莺莺",
                    source="new",
                    context_span="柳莺莺在醉花楼大堂弹琵琶。",
                )
            ]
        ),
    )

    saved = store.get_by_id(result.saved_ids[0])
    assert [call["task"] for call in gateway.calls] == [
        "npc_profile_update",
        "npc_profile_backfill",
    ]
    assert saved.core_motivation == "攒够赎身钱，拥有能自主选择客人与去处的生活。"
    assert saved.hidden_secret == "她暗中替城中几家商号传递消息，借此积累人脉。"
    assert saved.encounter_potentials == []


def test_backfill_can_promote_placeholder_name_and_preserve_alias():
    store = NPCProfileStore(":memory:")
    gateway = _Gateway(
        [
            {
                "npcs": [
                    {
                        "npc_index": 0,
                        "profile": {
                            "name": "二楼女子",
                            "aliases": [],
                            "public_summary": "醉花楼二楼雅间中参与大额生意谈判的女子。",
                            "role": "生意谈判方",
                            "appearance": "声音温婉中带媚。",
                            "personality_traits": ["从容", "精明"],
                            "known_abilities": ["商业谈判"],
                            "faction": None,
                            "description": None,
                            "current_location": "醉花楼二楼雅间",
                            "attitude_to_player": None,
                            "current_intent": "与李掌柜谈判一笔大生意",
                        },
                    }
                ]
            },
            {
                "name": "苏锦屏",
                "role": "行商出身的生意谈判方",
                "appearance": "声音温婉中带媚，举止从容。",
                "personality_traits": ["从容", "精明"],
                "known_abilities": ["商业谈判"],
                "faction": None,
                "core_motivation": "建立一张足以保护自身生意的合作网络。",
                "hidden_secret": "她正在隐瞒真正货主的身份。",
                "conflict_style": "先谈条件，谈不拢就用账目和人情施压。",
                "social_style": "语气温婉，话锋却常常直指利益核心。",
            },
        ]
    )

    result = update_npc_profiles_from_extraction(
        scene_text="二楼女子正在与李掌柜谈一笔大生意。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[],
        extraction=NPCExtractionResult(
            retrieved_npcs=[
                RetrievedNPC(
                    name="二楼女子",
                    source="new",
                    context_span="二楼女子正在与李掌柜谈一笔大生意。",
                )
            ]
        ),
    )

    saved = store.get_by_id(result.saved_ids[0])
    assert saved.name == "苏锦屏"
    assert "二楼女子" in saved.aliases
    assert saved.alias_hit_counts["二楼女子"] == 1


def test_existing_npc_update_does_not_trigger_profile_backfill():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            aliases=["裴姑娘"],
            public_summary="鸣玉坊琵琶名手。",
            role="琵琶名手",
            appearance="白衣抱琵琶。",
            personality_traits=["清冷"],
            known_abilities=["琵琶"],
            faction="鸣玉坊",
            description="常在鸣玉坊演奏。",
            current_location="鸣玉坊",
        )
    )
    existing = store.get_by_id(existing_id)
    gateway = _Gateway(
        [
            {
                "npcs": [
                    {
                        "npc_index": 0,
                        "profile": {
                            "name": "裴玉奴改名",
                            "aliases": ["乱加别名"],
                            "public_summary": "裴玉奴是鸣玉坊琵琶名手，已公开与萧云痕保持谨慎往来。",
                            "role": "不应被改写的身份",
                            "appearance": "不应被改写的外貌",
                            "personality_traits": ["不应被改写"],
                            "known_abilities": ["不应被改写"],
                            "faction": "不应被改写的阵营",
                            "description": "不应被改写的描述",
                            "current_location": "醉花楼",
                            "attitude_to_player": "谨慎",
                            "current_intent": "观察萧云痕",
                        },
                    }
                ]
            }
        ]
    )

    update_npc_profiles_from_extraction(
        scene_text="裴玉奴在醉花楼观察萧云痕。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        extraction=NPCExtractionResult(
            retrieved_npcs=[
                RetrievedNPC(
                    name="裴玉奴",
                    source="exact_match",
                    matched_npc_index=0,
                    matched_npc_name="裴玉奴",
                )
            ]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert [call["task"] for call in gateway.calls] == ["npc_profile_update"]
    assert updated.name == "裴玉奴"
    assert updated.aliases == ["裴姑娘"]
    assert updated.public_summary == "裴玉奴是鸣玉坊琵琶名手，已公开与萧云痕保持谨慎往来。"
    assert updated.role == "琵琶名手"
    assert updated.appearance == "白衣抱琵琶。"
    assert updated.personality_traits == ["清冷"]
    assert updated.known_abilities == ["琵琶"]
    assert updated.faction == "鸣玉坊"
    assert updated.description == "常在鸣玉坊演奏。"
    assert updated.current_location == "醉花楼"
    assert updated.attitude_to_player == "谨慎"
    assert updated.current_intent == "观察萧云痕"
    assert updated.core_motivation is None
    assert updated.hidden_secret is None
