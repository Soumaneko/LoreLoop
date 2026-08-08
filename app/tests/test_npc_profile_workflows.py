import json
from pathlib import Path

from app.domain.core_memory import CoreMemory
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.npc_profile.extractor import NPCProfileExtractor
from app.domain.npc_profile.updater import NPCProfileUpdater
from app.domain.npc_profile.schemas import NPCExtractionResult, NPCMention, NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.workflows import (
    attach_transient_alias_for_rename,
    backfill_new_npc_profile,
    prune_expired_transient_aliases,
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


def test_backfill_core_memory_projection_excludes_protagonist_profile():
    from app.domain.core_memory.schemas import CoreMemoryItem

    core_memory = CoreMemory(items=[
        CoreMemoryItem(
            id="core_world_identity",
            kind="world_invariant",
            strength="hard_constraint",
            source="player",
            content="这是一个以航海贸易为主的世界。",
        ),
        CoreMemoryItem(
            id="core_player_identity",
            kind="protagonist_invariant",
            strength="hard_constraint",
            source="player",
            content="主角是萧云痕，曾为镖局总镖头。",
        ),
    ])

    projected = core_memory.without_protagonist()

    assert [item.id for item in projected.items] == ["core_world_identity"]
    assert all(
        item.kind != "protagonist_invariant"
        for item in projected.items
    )


def test_npc_agents_use_scene_facts_without_director_resource_dependencies():
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
    )

    assert "director_events_identity_json" not in extractor_gateway.calls[0]["input_data"]
    assert "director_events_identity_json" not in updater_gateway.calls[0]["input_data"]


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


def test_backfill_prompt_requires_proper_name_for_descriptor_npcs():
    prompt = Path("app/prompts/director/skills/npc_profile_backfill_v1.md").read_text(
        encoding="utf-8"
    )

    assert "{creative_brief_json}" in prompt
    assert "baseline_temperament" in prompt
    assert "必须是一个专有姓名" in prompt
    assert "必须为 TA 创造一个符合世界观的专有姓名" in prompt
    assert "来历不明者" in prompt
    assert "筑基初期女修" in prompt
    assert "练气巅峰采药女" in prompt
    assert "不得沿用代称作为姓名" in prompt
    assert "原代称会由系统自动保留为临时别名" in prompt
    assert "不要改名" not in prompt


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


def test_new_npc_candidate_is_not_saved_or_backfilled():
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

    assert [call["task"] for call in gateway.calls] == ["npc_profile_update"]
    assert result.saved_ids == []
    assert len(result.new_profiles) == 1
    assert result.new_profiles[0].name == "柳莺莺"
    assert store.list_all() == []


def test_new_npc_without_enrich_is_dropped():
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

    assert [call["task"] for call in gateway.calls] == ["npc_profile_update"]
    assert result.saved_ids == []
    assert len(result.new_profiles) == 1
    assert result.new_profiles[0].name == "二楼女子"
    assert store.list_all() == []


def test_backfill_can_promote_placeholder_name_and_preserve_alias():
    store = NPCProfileStore(":memory:")
    gateway = _Gateway(
        [
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

    profile = NPCProfile(
        name="二楼女子",
        aliases=[],
        public_summary="醉花楼二楼雅间中参与大额生意谈判的女子。",
        role="生意谈判方",
        appearance="声音温婉中带媚。",
        personality_traits=["从容", "精明"],
        known_abilities=["商业谈判"],
        current_location="醉花楼二楼雅间",
        current_intent="与李掌柜谈判一笔大生意",
    )
    backfilled = backfill_new_npc_profile(
        profile=profile,
        existing_profiles=[],
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
    )
    attach_transient_alias_for_rename(backfilled, old_name="二楼女子", turn_index=3)

    assert backfilled.name == "苏锦屏"
    assert "二楼女子" not in backfilled.aliases
    assert backfilled.transient_aliases["二楼女子"] == 6


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
    assert store.get_by_name("林川") is None
    assert result.new_profiles[0].name == "林川"
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


def test_transient_alias_attach_and_store_lookup():
    store = NPCProfileStore(":memory:")
    profile = NPCProfile(name="沈青萝")
    attach_transient_alias_for_rename(
        profile,
        old_name="筑基初期女修",
        turn_index=5,
    )
    assert profile.transient_aliases == {"筑基初期女修": 8}
    attach_transient_alias_for_rename(
        profile,
        old_name="筑基初期女修",
        turn_index=9,
    )
    assert profile.transient_aliases == {"筑基初期女修": 8}

    npc_id = store.upsert(profile)
    assert store.get_by_name("筑基初期女修").npc_id == npc_id


def test_transient_alias_does_not_duplicate_permanent_alias():
    profile = NPCProfile(name="沈青萝", aliases=["筑基初期女修"])
    attach_transient_alias_for_rename(
        profile,
        old_name="筑基初期女修",
        turn_index=5,
    )
    assert "筑基初期女修" not in profile.transient_aliases


def test_prune_expired_transient_aliases_on_turn_advance():
    store = NPCProfileStore(":memory:")
    npc_id = store.upsert(
        NPCProfile(
            name="沈青萝",
            transient_aliases={"筑基初期女修": 6},
        )
    )
    assert store.get_by_name("筑基初期女修") is not None

    saved_ids = sync_npc_profile_absence_from_current_state(
        npc_store=store,
        current_state=_state(),
        turn_index=7,
    )

    assert npc_id in saved_ids
    saved = store.get_by_id(npc_id)
    assert saved.transient_aliases == {}
    assert store.get_by_name("筑基初期女修") is None


def test_transient_alias_still_matches_before_expiry():
    profile = NPCProfile(name="沈青萝", transient_aliases={"筑基初期女修": 6})
    assert prune_expired_transient_aliases(profile, turn_index=6) is False
    assert "筑基初期女修" in profile.transient_aliases
