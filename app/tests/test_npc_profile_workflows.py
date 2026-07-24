import json
from pathlib import Path

from app.domain.core_memory import CoreMemory
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.npc_profile.extractor import NPCProfileExtractor
from app.domain.npc_profile.updater import NPCProfileUpdater
from app.domain.npc_profile.schemas import NPCExtractionResult, NPCMention, NPCProfile
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
        player=PlayerState(name="萧云痕"),
    )


def _director_event() -> DirectorEvent:
    return DirectorEvent(
        role="arc",
        title="失窃旧书",
        premise="希尔达寻找自己失窃的旧书。",
        objective="找回旧书。",
        bridge_anchors=["旧书"],
        milestones=[
            "希尔达留下寻物便条。",
            "希尔达追查旧书去向。",
            "旧书事件形成结果。",
        ],
        milestone_interval_steps=[2, 2],
        created_turn=1,
    )


def test_npc_agents_receive_all_director_event_identity_contexts():
    arc = _director_event()
    ambient = _director_event().model_copy(
        update={"role": "ambient", "title": "城外商队"}
    )
    events = [arc, ambient]
    extractor_gateway = _Gateway([{"mentions": []}])
    NPCProfileExtractor(extractor_gateway).extract(
        scene_facts="寻物便条张贴者是一名女性。",
    )

    updater_gateway = _Gateway([{"results": []}])
    NPCProfileUpdater(updater_gateway).update(
        scene_facts="寻物便条张贴者是一名女性。",
        current_state=_state(),
        existing_profiles=[],
        extraction=NPCExtractionResult(mentions=[
            NPCMention(name="寻物便条张贴者", context_span="寻物便条张贴者是一名女性。")
        ]),
        core_memory=CoreMemory(items=[]),
        director_events=events,
    )

    expected = [
        {"role": event.role, "premise": event.premise, "milestones": event.milestones}
        for event in events
    ]
    assert "director_events_identity_json" not in extractor_gateway.calls[0]["input_data"]
    assert json.loads(
        updater_gateway.calls[0]["input_data"]["director_events_identity_json"]
    ) == expected


def test_npc_updater_prompt_treats_new_hints_as_optional_candidates():
    prompt = Path("app/prompts/confirmation/npc_profile_update_v1.md").read_text(
        encoding="utf-8"
    )

    assert '`source="existing"`' in prompt
    assert '`source="unresolved"`' in prompt
    assert "拒绝是默认行为，创建是例外" in prompt
    assert "“林川”和“陆川”默认是两个不同 NPC" in prompt
    assert "`turn_observation`" in prompt
    assert "每一个 `candidate_index`" in prompt
    assert "turn_observation 不会" not in prompt
    assert "turn_observation` 不会" not in prompt


def test_store_adds_unambiguous_given_name_alias():
    store = NPCProfileStore(":memory:")
    npc_id = store.upsert(NPCProfile(name="希尔达·布拉斯科特"))

    saved = store.get_by_id(npc_id)
    assert saved.aliases == ["希尔达"]
    assert store.get_by_name("希尔达").npc_id == npc_id


def test_store_skips_conflicting_given_name_alias():
    store = NPCProfileStore(":memory:")
    store.upsert(NPCProfile(name="希尔达"))
    npc_id = store.upsert(NPCProfile(name="希尔达·布拉斯科特"))

    saved = store.get_by_id(npc_id)
    assert "希尔达" not in saved.aliases


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
        player=PlayerState(name="萧云痕"),
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
        player=PlayerState(name="萧云痕"),
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
                "results": [
                    {
                        "candidate_index": 0,
                        "resolution": "new",
                        "matched_npc_index": None,
                        "persist_alias": False,
                        "turn_observation": "柳莺莺正在醉花楼大堂弹奏琵琶。",
                        "profile_patch": None,
                        "new_profile": {
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
        scene_facts="柳莺莺在醉花楼大堂弹琵琶。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[],
        extraction=NPCExtractionResult(
            mentions=[
                NPCMention(
                    name="柳莺莺",
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
                "results": [
                    {
                        "candidate_index": 0,
                        "resolution": "new",
                        "matched_npc_index": None,
                        "persist_alias": False,
                        "turn_observation": "二楼女子正在参与一笔生意谈判。",
                        "profile_patch": None,
                        "new_profile": {
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
        scene_facts="二楼女子正在与李掌柜谈一笔大生意。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[],
        extraction=NPCExtractionResult(
            mentions=[
                NPCMention(
                    name="二楼女子",
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
                "results": [
                    {
                        "candidate_index": 0,
                        "resolution": "existing",
                        "matched_npc_index": 0,
                        "persist_alias": False,
                        "turn_observation": "裴玉奴本回合在醉花楼观察萧云痕。",
                        "profile_patch": {
                            "public_summary": "裴玉奴是鸣玉坊琵琶名手，已公开与萧云痕保持谨慎往来。",
                            "current_location": "醉花楼",
                            "attitude_to_player": "谨慎",
                            "current_intent": "观察萧云痕",
                        },
                        "new_profile": None,
                    }
                ]
            }
        ]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴在醉花楼观察萧云痕。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        extraction=NPCExtractionResult(
            mentions=[
                NPCMention(
                    name="裴玉奴",
                    context_span="裴玉奴在醉花楼观察萧云痕。",
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


def test_similar_name_is_unresolved_and_can_create_distinct_npc():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(name="陆川", role="青云门弟子", current_location="山门")
    )
    existing = store.get_by_id(existing_id)
    gateway = _Gateway(
        [
            {
                "results": [
                    {
                        "candidate_index": 0,
                        "resolution": "new",
                        "matched_npc_index": None,
                        "persist_alias": False,
                        "turn_observation": "林川向萧云痕说明自己的书院来历。",
                        "profile_patch": None,
                        "new_profile": {
                            "name": "林川",
                            "aliases": [],
                            "public_summary": "来自白鹤书院的年轻书生。",
                            "role": "白鹤书院书生",
                            "appearance": None,
                            "personality_traits": [],
                            "known_abilities": [],
                            "faction": "白鹤书院",
                            "description": None,
                            "current_location": "醉花楼",
                            "attitude_to_player": "友善",
                            "current_intent": "向萧云痕请教",
                        },
                    }
                ]
            },
            {
                "name": "林川",
                "role": "白鹤书院书生",
                "appearance": "青衫书生打扮。",
                "personality_traits": ["好学", "坦率"],
                "known_abilities": ["经义"],
                "faction": "白鹤书院",
                "core_motivation": "在游学中验证自己的学问。",
                "hidden_secret": "私下追查一封失踪的书信。",
                "conflict_style": "先讲理，受阻后寻找旁证。",
                "social_style": "坦率发问，不喜绕弯。",
            },
        ]
    )

    result = update_npc_profiles_from_extraction(
        scene_facts="林川自称来自白鹤书院，并向萧云痕请教。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        extraction=NPCExtractionResult(mentions=[
            NPCMention(
                name="林川",
                context_span="林川自称来自白鹤书院，并向萧云痕请教。",
            )
        ]),
    )

    assert store.get_by_id(existing_id).aliases == []
    assert store.get_by_name("林川").npc_id in result.saved_ids
    candidates = json.loads(gateway.calls[0]["input_data"]["npc_candidates_json"])
    assert candidates[0]["source"] == "unresolved"


def test_exact_match_and_updater_identity_resolution_share_one_update_call():
    store = NPCProfileStore(":memory:")
    exact_id = store.upsert(NPCProfile(name="裴玉奴", aliases=["裴姑娘"]))
    alias_target_id = store.upsert(NPCProfile(name="陆远舟", role="镖师"))
    existing = store.list_all()
    exact_index = next(i for i, profile in enumerate(existing) if profile.npc_id == exact_id)
    alias_index = next(
        i for i, profile in enumerate(existing) if profile.npc_id == alias_target_id
    )
    gateway = _Gateway(
        [{
            "results": [
                {
                    "candidate_index": 0,
                    "resolution": "existing",
                    "matched_npc_index": exact_index,
                    "persist_alias": False,
                    "turn_observation": "裴姑娘本回合出现在醉花楼。",
                    "profile_patch": {
                        "current_location": "醉花楼",
                    },
                    "new_profile": None,
                },
                {
                    "candidate_index": 1,
                    "resolution": "alias_match",
                    "matched_npc_index": alias_index,
                    "persist_alias": True,
                    "turn_observation": "远舟镖头向裴姑娘抱拳。",
                    "profile_patch": None,
                    "new_profile": None,
                },
            ]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴姑娘在醉花楼见到了自称远舟镖头的陆远舟。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=existing,
        extraction=NPCExtractionResult(mentions=[
            NPCMention(name="裴姑娘", context_span="裴姑娘在醉花楼。"),
            NPCMention(
                name="远舟镖头",
                context_span="自称远舟镖头的陆远舟向裴姑娘抱拳。",
            ),
        ]),
    )

    assert [call["task"] for call in gateway.calls] == ["npc_profile_update"]
    assert store.get_by_id(exact_id).current_location == "醉花楼"
    assert store.get_by_id(exact_id).alias_hit_counts["裴姑娘"] == 1
    assert "远舟镖头" in store.get_by_id(alias_target_id).aliases


def test_existing_no_op_patch_keeps_observation_without_profile_update():
    existing = NPCProfile(
        name="苏云袖",
        public_summary="水木双灵根的合欢宗弟子。",
        current_location="合欢殿",
        attitude_to_player="信任",
        current_intent="修习水系治愈术",
        source_scene_ids=["scene_008"],
        confidence=0.7,
    )
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": "萧云痕本回合提到了苏云袖。",
                "profile_patch": {
                    "public_summary": "水木双灵根的合欢宗弟子。",
                    "current_location": "合欢殿",
                    "attitude_to_player": "信任",
                    "current_intent": "修习水系治愈术",
                },
                "new_profile": None,
            }]
        }]
    )

    result = NPCProfileUpdater(gateway).update(
        scene_facts="萧云痕提到了苏云袖。",
        current_state=_state(),
        existing_profiles=[existing],
        extraction=NPCExtractionResult(mentions=[
            NPCMention(name="苏云袖", context_span="萧云痕提到了苏云袖。")
        ]),
        core_memory=CoreMemory(items=[]),
    )

    assert result.profiles == []
    assert result.turn_observations == [
        ("苏云袖", "萧云痕本回合提到了苏云袖。")
    ]
    assert result.exact_mentions == [(existing.npc_id, "苏云袖")]
