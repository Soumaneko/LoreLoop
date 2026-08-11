import json
from pathlib import Path

import pytest

from app.domain.confirmed_history import HistoryDocument
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
    prune_npc_transient_aliases_from_current_state,
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


def _evidence_documents(scene_text: str) -> dict[str, HistoryDocument]:
    from app.domain.confirmed_history import HistoryDocument, build_turn_documents

    return {
        document.document_id: document
        for document in build_turn_documents(
            scene_id="scene_001",
            turn_index=0,
            user_input="",
            scene_text=scene_text,
        )
        if document.source_type == "scene_paragraph"
    }


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
    assert "ignore" in prompt
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
    assert "必须为 TA 创造一个符合世界观、独特的专有姓名" in prompt
    assert "不得沿用代称作为姓名" in prompt
    assert "则保持原名" in prompt
    assert "来历不明者" in prompt
    assert "筑基初期女修" in prompt
    assert "练气巅峰采药女" in prompt
    assert "优先沿用公开档案" in prompt
    assert "必须独立于主角当前行动" in prompt
    assert "并体现能力边界" in prompt
    assert "优先依据 Scene Facts 和公开档案" in prompt
    assert "`identity` → `behavior` → `shell`" in prompt
    assert "personality_trait" in prompt
    assert "外部可观察" in prompt
    assert "`name`（短标签）与 `description`" in prompt
    assert "不要改名" not in prompt


def test_creation_prompts_describe_every_output_schema_field():
    from app.domain.director_npc.schemas import DirectorNPCCreateOutput
    from app.domain.npc_profile.schemas import NPCProfileBackfillOutput

    cases = [
        (
            "app/prompts/director/skills/director_npc_create_v1.md",
            DirectorNPCCreateOutput,
        ),
        (
            "app/prompts/director/skills/npc_profile_backfill_v1.md",
            NPCProfileBackfillOutput,
        ),
    ]
    for path, schema in cases:
        prompt = Path(path).read_text(encoding="utf-8")
        for field in schema.model_fields:
            assert field in prompt, (
                f"{path} 缺少输出字段 {field!r} 的字段说明"
            )


def test_update_input_allows_empty_ability_description_and_enforces_limits():
    from pydantic import ValidationError

    from app.domain.npc_profile.schemas import NPCProfileUpdateInput

    ok = NPCProfileUpdateInput(
        name="林澈",
        personality_trait="   ",
        known_abilities=[{"name": "急救", "description": ""}],
    )
    assert ok.personality_trait is None
    assert ok.known_abilities[0].description == ""

    with pytest.raises(ValidationError):
        NPCProfileUpdateInput(name="林澈", personality_trait="x" * 41)
    with pytest.raises(ValidationError):
        NPCProfileUpdateInput(
            name="林澈",
            known_abilities=[
                {"name": f"a{i}", "description": "d"}
                for i in range(6)
            ],
        )


def test_backfill_shell_rejects_empty_ability_descriptions():
    from pydantic import ValidationError

    from app.domain.npc_profile.schemas import NPCProfileBackfillShell

    with pytest.raises(ValidationError):
        NPCProfileBackfillShell(
            known_abilities=[{"name": "急救", "description": "  "}],
            appearance="风衣。",
        )


def test_updater_new_profile_rejects_empty_ability_descriptions():
    from pydantic import ValidationError

    from app.domain.npc_profile.schemas import NPCProfileCandidateResult

    with pytest.raises(ValidationError):
        NPCProfileCandidateResult(
            candidate_index=0,
            resolution="new",
            new_profile={
                "name": "林澈",
                "known_abilities": [{"name": "急救", "description": " "}],
            },
        )


def test_persistent_known_ability_allows_empty_description_for_player_edits():
    from app.domain.npc_profile.schemas import KnownAbility

    ability = KnownAbility(name="急救", description="   ")
    assert ability.description == ""


def test_llm_ability_schemas_require_description_but_player_schema_does_not():
    from app.domain.director_npc.schemas import (
        KnownAbility as DirectorKnownAbility,
        DirectorNPCShell,
    )
    from app.domain.npc_profile.schemas import (
        KnownAbility,
        LLMKnownAbility,
        NPCNewProfileOutput,
        NPCProfileBackfillShell,
    )

    assert KnownAbility.model_json_schema()["required"] == ["name"]
    assert LLMKnownAbility.model_json_schema()["required"] == ["name", "description"]
    assert DirectorKnownAbility.model_json_schema()["required"] == [
        "name",
        "description",
    ]
    for container, strict_name in (
        (DirectorNPCShell, "KnownAbility"),
        (NPCProfileBackfillShell, "LLMKnownAbility"),
        (NPCNewProfileOutput, "LLMKnownAbility"),
    ):
        defs = container.model_json_schema()["$defs"]
        assert defs[strict_name]["required"] == ["name", "description"], (
            f"{container.__name__} 的 JSON Schema 必须要求能力 description"
        )


def test_backfill_and_create_prompts_share_hierarchical_field_contract():
    backfill = Path(
        "app/prompts/director/skills/npc_profile_backfill_v1.md"
    ).read_text(encoding="utf-8")
    create = Path(
        "app/prompts/director/skills/director_npc_create_v1.md"
    ).read_text(encoding="utf-8")

    for prompt in (backfill, create):
        assert "`identity` → `behavior` → `shell`" in prompt
        assert "以 `identity` 的核心字段为准" in prompt
        assert "`personality_trait`" in prompt
        assert "外部可观察" in prompt
        assert "不写万能能力" in prompt


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


def test_prune_npc_transient_aliases_from_current_state():
    store = NPCProfileStore(":memory:")
    expired_id = store.upsert(
        NPCProfile(name="裴玉奴", transient_aliases={"裴姑娘": 2})
    )
    retained_id = store.upsert(
        NPCProfile(name="周泰", transient_aliases={"周都头": 5})
    )

    saved_ids = prune_npc_transient_aliases_from_current_state(
        npc_store=store,
        turn_index=3,
    )

    assert saved_ids == [expired_id]
    assert store.get_by_id(expired_id).transient_aliases == {}
    assert store.get_by_id(retained_id).transient_aliases == {"周都头": 5}


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
                        "temporary_patch": None,
                        "new_profile": {
                            "name": "柳莺莺",
                            "aliases": [],
                            "public_summary": "醉花楼花魁，琴艺出众。",
                            "role": "醉花楼花魁",
                            "appearance": "青衣抱琵琶，姿态温婉。",
                            "personality_trait": "接客前先调一遍弦，节奏对了才开口说话",
                            "known_abilities": [
                                {"name": "琵琶", "description": "演奏时能以曲调应和现场气氛"}
                            ],
                            "faction": "醉花楼",
                            "last_known_location": "醉花楼",
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
                        "temporary_patch": None,
                        "new_profile": {
                            "name": "二楼女子",
                            "aliases": [],
                            "public_summary": "醉花楼二楼雅间中参与大额生意谈判的女子。",
                            "role": "生意谈判方",
                            "appearance": "声音温婉中带媚。",
                            "personality_trait": "开口先夸对方生意，再慢慢把话题引到账目上",
                            "known_abilities": [
                                {"name": "商业谈判", "description": "凭账目与行情拿捏谈判筹码"}
                            ],
                            "faction": None,
                            "last_known_location": "醉花楼二楼雅间",
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
                "identity": {
                    "core_motivation": "建立一张足以保护自身生意的合作网络。",
                    "hidden_secret": "她正在隐瞒真正货主的身份。",
                    "role": "行商出身的生意谈判方",
                    "faction": None,
                },
                "behavior": {
                    "conflict_style": "先谈条件，谈不拢就用账目和人情施压。",
                    "social_style": "语气温婉，话锋却常常直指利益核心。",
                    "personality_trait": "开口先夸对方生意，再慢慢把话题引到账目上",
                },
                "shell": {
                    "known_abilities": [{"name": "商业谈判", "description": "凭账目与行情拿捏谈判筹码"}],
                    "appearance": "声音温婉中带媚，举止从容。",
                },
            },
        ]
    )

    profile = NPCProfile(
        name="二楼女子",
        aliases=[],
        public_summary="醉花楼二楼雅间中参与大额生意谈判的女子。",
        role="生意谈判方",
        appearance="声音温婉中带媚。",
        personality_trait="开口先夸对方生意，再慢慢把话题引到账目上",
        known_abilities=[],
        last_known_location="醉花楼二楼雅间",
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
            personality_trait="登台前反复试弦，不合意绝不开口",
            known_abilities=[{"name": "琵琶", "description": "演奏时能以曲调应和现场气氛"}],
            faction="鸣玉坊",
            last_known_location="鸣玉坊",
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
                        "temporary_patch": {
                            "last_known_location": "醉花楼",
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
    assert updated.public_summary == "鸣玉坊琵琶名手。"
    assert updated.role == "琵琶名手"
    assert updated.appearance == "白衣抱琵琶。"
    assert updated.personality_trait == "登台前反复试弦，不合意绝不开口"
    assert [ability.model_dump() for ability in updated.known_abilities] == [
        {"name": "琵琶", "description": "演奏时能以曲调应和现场气氛"}
    ]
    assert updated.faction == "鸣玉坊"
    assert updated.last_known_location == "醉花楼"
    assert updated.attitude_to_player == "谨慎"
    assert updated.current_intent == "观察萧云痕"
    assert updated.core_motivation is None
    assert updated.hidden_secret is None


def test_similar_name_is_unresolved_and_can_create_distinct_npc():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(name="陆川", role="青云门弟子", last_known_location="山门")
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
                        "temporary_patch": None,
                        "new_profile": {
                            "name": "林川",
                            "aliases": [],
                            "public_summary": "来自白鹤书院的年轻书生。",
                            "role": "白鹤书院书生",
                            "appearance": None,
                            "personality_trait": None,
                            "known_abilities": [],
                            "faction": "白鹤书院",
                            "last_known_location": "醉花楼",
                            "attitude_to_player": "友善",
                            "current_intent": "向萧云痕请教",
                        },
                    }
                ]
            },
            {
                "name": "林川",
                "identity": {
                    "core_motivation": "在游学中验证自己的学问。",
                    "hidden_secret": "私下追查一封失踪的书信。",
                    "role": "白鹤书院书生",
                    "faction": "白鹤书院",
                },
                "behavior": {
                    "conflict_style": "先讲理，受阻后寻找旁证。",
                    "social_style": "坦率发问，不喜绕弯。",
                    "personality_trait": "遇到新说法先引一段书上的话再下判断",
                },
                "shell": {
                    "known_abilities": [{"name": "经义", "description": "引经据典辨析论题"}],
                    "appearance": "青衫书生打扮。",
                },
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
                    "temporary_patch": {
                        "last_known_location": "醉花楼",
                    },
                    "new_profile": None,
                },
                {
                    "candidate_index": 1,
                    "resolution": "alias_match",
                    "matched_npc_index": alias_index,
                    "persist_alias": True,
                    "turn_observation": "远舟镖头向裴姑娘抱拳。",
                    "temporary_patch": None,
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
    assert store.get_by_id(exact_id).last_known_location == "醉花楼"
    assert store.get_by_id(exact_id).alias_hit_counts["裴姑娘"] == 1
    assert "远舟镖头" in store.get_by_id(alias_target_id).aliases


def test_existing_no_op_patch_keeps_observation_without_profile_update():
    existing = NPCProfile(
        name="苏云袖",
        public_summary="水木双灵根的合欢宗弟子。",
        last_known_location="合欢殿",
        attitude_to_player="信任",
        current_intent="修习水系治愈术",
        source_scene_ids=["scene_008"],
    )
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": "萧云痕本回合提到了苏云袖。",
                "temporary_patch": {
                        "last_known_location": "合欢殿",
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

    saved_ids = prune_npc_transient_aliases_from_current_state(
        npc_store=store,
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


def test_updater_prompt_defines_temporary_and_long_term_channels():
    prompt = Path("app/prompts/confirmation/npc_profile_update_v1.md").read_text(
        encoding="utf-8"
    )

    assert "`temporary_patch`" in prompt
    assert "`long_term_patch`" in prompt
    assert "`field_evidence`" in prompt
    assert "Scene Paragraph Evidence" in prompt
    assert "scene_paragraph_evidence_json" in prompt
    assert "每回合状态更新通道" in prompt
    assert "不会过期" in prompt
    assert "极高风险字段" in prompt
    assert "document_id" in prompt
    assert "profile_patch" not in prompt


def test_long_term_patch_updates_existing_profile_fields():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            public_summary="鸣玉坊琵琶名手。",
            role="琵琶名手",
            personality_trait="登台前反复试弦",
            faction="鸣玉坊",
            core_motivation="守住鸣玉坊的牌面",
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴如今常驻醉花楼献艺。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "public_summary": "鸣玉坊琵琶名手，如今常驻醉花楼献艺。",
                    "role": "醉花楼琵琶名手",
                    "personality_trait": "登台前反复试弦，不合意绝不开口",
                    "faction": "醉花楼",
                    "field_evidence": {
                        "public_summary": ["turn_0_p001"],
                        "role": ["turn_0_p001"],
                        "personality_trait": ["turn_0_p001"],
                        "faction": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴如今常驻醉花楼献艺。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴常驻醉花楼献艺。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert updated.public_summary == "鸣玉坊琵琶名手，如今常驻醉花楼献艺。"
    assert updated.role == "醉花楼琵琶名手"
    assert updated.personality_trait == "登台前反复试弦，不合意绝不开口"
    assert updated.faction == "醉花楼"
    assert updated.core_motivation == "守住鸣玉坊的牌面"


def test_high_risk_field_without_evidence_is_dropped():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            public_summary="鸣玉坊琵琶名手。",
            faction="鸣玉坊",
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴出现在醉花楼。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "public_summary": "被推断改写后的身份描述。",
                    "faction": "青云门",
                    "field_evidence": {
                        "faction": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    result = update_npc_profiles_from_extraction(
        scene_facts="裴玉奴出现在醉花楼。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴出现在醉花楼。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert updated.public_summary == "鸣玉坊琵琶名手。"
    assert updated.faction == "青云门"
    assert [(w.field_name, w.code) for w in result.warnings] == [
        ("public_summary", "long_term_field_evidence_missing")
    ]


def test_field_evidence_unknown_key_raises():
    from pydantic import ValidationError

    from app.domain.npc_profile.schemas import ExistingNPCProfileLongTermPatch

    with pytest.raises(ValidationError, match="unknown long-term field evidence keys"):
        ExistingNPCProfileLongTermPatch.model_validate({
            "role": "名手",
            "field_evidence": {"typo_field": ["turn_0_p001"]},
        })


def test_field_evidence_empty_list_raises():
    from pydantic import ValidationError

    from app.domain.npc_profile.schemas import ExistingNPCProfileLongTermPatch

    with pytest.raises(ValidationError, match="must not be empty"):
        ExistingNPCProfileLongTermPatch.model_validate({
            "role": "名手",
            "field_evidence": {"role": []},
        })


def test_field_evidence_duplicate_ids_raise():
    from pydantic import ValidationError

    from app.domain.npc_profile.schemas import ExistingNPCProfileLongTermPatch

    with pytest.raises(ValidationError, match="must not repeat"):
        ExistingNPCProfileLongTermPatch.model_validate({
            "role": "名手",
            "field_evidence": {"role": ["turn_0_p001", "turn_0_p001"]},
        })


def test_long_term_fabricated_evidence_drops_field_and_warns():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            public_summary="鸣玉坊琵琶名手。",
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴出现在醉花楼。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "public_summary": "被编造证据改写的描述。",
                    "field_evidence": {
                        "public_summary": ["fabricated_document_id"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    result = update_npc_profiles_from_extraction(
        scene_facts="裴玉奴出现在醉花楼。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴出现在醉花楼。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert updated.public_summary == "鸣玉坊琵琶名手。"
    assert result.saved_ids == []
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.npc_name == "裴玉奴"
    assert warning.field_name == "public_summary"
    assert warning.code == "long_term_field_evidence_dropped"
    assert warning.evidence_ids == ("fabricated_document_id",)


def test_long_term_evidence_must_be_scene_paragraph_of_this_turn():
    from app.domain.confirmed_history import HistoryDocument

    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            public_summary="鸣玉坊琵琶名手。",
        )
    )
    existing = store.get_by_id(existing_id)
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "public_summary": "改写的描述。",
                    "field_evidence": {
                        "public_summary": ["turn_0_input"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    player_input_document = HistoryDocument(
        document_id="turn_0_input",
        source_type="player_input",
        turn_index=0,
        scene_id="scene_001",
        text="裴玉奴改写了描述。",
    )
    result = update_npc_profiles_from_extraction(
        scene_facts="裴玉奴出现在醉花楼。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents={"turn_0_input": player_input_document},
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴出现在醉花楼。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert updated.public_summary == "鸣玉坊琵琶名手。"
    assert result.saved_ids == []
    assert result.warnings[0].evidence_ids == ("turn_0_input",)


def test_long_term_evidence_from_other_turn_is_dropped():
    from app.domain.confirmed_history import HistoryDocument

    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            public_summary="鸣玉坊琵琶名手。",
        )
    )
    existing = store.get_by_id(existing_id)
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "public_summary": "改写的描述。",
                    "field_evidence": {
                        "public_summary": ["turn_1_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    other_turn_document = HistoryDocument(
        document_id="turn_1_p001",
        source_type="scene_paragraph",
        turn_index=1,
        scene_id="scene_001",
        text="裴玉奴改写了描述。",
    )
    result = update_npc_profiles_from_extraction(
        scene_facts="裴玉奴出现在醉花楼。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents={"turn_1_p001": other_turn_document},
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴出现在醉花楼。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert updated.public_summary == "鸣玉坊琵琶名手。"
    assert result.saved_ids == []
    assert result.warnings[0].code == "long_term_field_evidence_dropped"
    assert result.warnings[0].evidence_ids == ("turn_1_p001",)


def test_long_term_evidence_without_turn_index_raises():
    store = NPCProfileStore(":memory:")
    existing = store.upsert(NPCProfile(name="裴玉奴"))
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "role": "名手",
                    "field_evidence": {"role": ["turn_0_p001"]},
                },
                "new_profile": None,
            }]
        }]
    )
    scene_text = "裴玉奴是醉花楼名手。"

    with pytest.raises(ValueError, match="turn_index is required"):
        update_npc_profiles_from_extraction(
            scene_facts="裴玉奴是醉花楼名手。",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
            gateway=gateway,
            npc_store=store,
            existing_profiles=[store.get_by_id(existing)],
            evidence_documents=_evidence_documents(scene_text),
            extraction=NPCExtractionResult(
                mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴是醉花楼名手。")]
            ),
        )


def test_long_term_drop_warning_order_is_deterministic():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            public_summary="鸣玉坊琵琶名手。",
            faction="鸣玉坊",
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴出现在醉花楼。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "public_summary": "被改写。",
                    "faction": "青云门",
                },
                "new_profile": None,
            }]
        }]
    )

    result = update_npc_profiles_from_extraction(
        scene_facts="裴玉奴出现在醉花楼。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴出现在醉花楼。")]
        ),
    )

    assert [w.field_name for w in result.warnings] == [
        "public_summary",
        "faction",
    ]


def test_long_term_blank_string_does_not_clear_field():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            role="琵琶名手",
            public_summary="鸣玉坊琵琶名手。",
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴出现在醉花楼。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "role": "",
                    "field_evidence": {
                        "role": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴出现在醉花楼。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴出现在醉花楼。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert updated.role == "琵琶名手"


def test_long_term_known_abilities_skip_duplicates_without_wasting_capacity():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            known_abilities=[
                {"name": "琵琶", "description": "a"},
                {"name": "唱曲", "description": "b"},
                {"name": "读账", "description": "c"},
                {"name": "待客", "description": "d"},
            ],
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴新学了轻功。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "known_abilities": {
                        "add": [
                            {"name": "琵琶", "description": "a"},
                            {"name": "轻功", "description": "e"},
                        ]
                    },
                    "field_evidence": {
                        "known_abilities": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴新学了轻功。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴新学了轻功。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert len(updated.known_abilities) == 5
    assert [a.name for a in updated.known_abilities] == [
        "琵琶",
        "唱曲",
        "读账",
        "待客",
        "轻功",
    ]


def test_long_term_known_abilities_append_delta_dedup_and_cap():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            known_abilities=[{"name": "琵琶", "description": "以曲调应和现场气氛"}],
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴能唱曲、读账。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "known_abilities": {
                        "add": [
                            {"name": "琵琶", "description": "以曲调应和现场气氛"},
                            {"name": "唱曲", "description": "以婉转唱腔吸引宾客"},
                            {"name": "读账", "description": "能快速核对流水账目"},
                        ]
                    },
                    "field_evidence": {
                        "known_abilities": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴能唱曲、读账。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴能唱曲、读账。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert [(a.name, a.description) for a in updated.known_abilities] == [
        ("琵琶", "以曲调应和现场气氛"),
        ("唱曲", "以婉转唱腔吸引宾客"),
        ("读账", "能快速核对流水账目"),
    ]


def test_long_term_known_abilities_cap_at_five():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            known_abilities=[
                {"name": "琵琶", "description": "a"},
                {"name": "唱曲", "description": "b"},
                {"name": "读账", "description": "c"},
                {"name": "待客", "description": "d"},
            ],
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴也会剑术和轻功。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "known_abilities": {
                        "add": [
                            {"name": "剑术", "description": "e"},
                            {"name": "轻功", "description": "f"},
                        ]
                    },
                    "field_evidence": {
                        "known_abilities": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴也会剑术和轻功。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴也会剑术和轻功。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert len(updated.known_abilities) == 5
    assert [a.name for a in updated.known_abilities] == [
        "琵琶",
        "唱曲",
        "读账",
        "待客",
        "剑术",
    ]


def test_long_term_known_abilities_cap_respects_full_abilities():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            known_abilities=[
                {"name": "琵琶", "description": "a"},
                {"name": "唱曲", "description": "b"},
                {"name": "读账", "description": "c"},
                {"name": "待客", "description": "d"},
                {"name": "奉茶", "description": "e"},
            ],
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴也会剑术。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "known_abilities": {
                        "add": [
                            {"name": "剑术", "description": "f"},
                        ]
                    },
                    "field_evidence": {
                        "known_abilities": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴也会剑术。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴也会剑术。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert len(updated.known_abilities) == 5
    assert [a.name for a in updated.known_abilities] == [
        "琵琶",
        "唱曲",
        "读账",
        "待客",
        "奉茶",
    ]


def test_long_term_no_op_patch_is_skipped():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            public_summary="鸣玉坊琵琶名手。",
            source_scene_ids=["scene_008"],
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴出现在醉花楼。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": None,
                "long_term_patch": {
                    "public_summary": "鸣玉坊琵琶名手。",
                    "field_evidence": {
                        "public_summary": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴出现在醉花楼。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴出现在醉花楼。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert updated.public_summary == "鸣玉坊琵琶名手。"
    assert updated.source_scene_ids == ["scene_008"]


def test_temporary_and_long_term_patch_combine_in_one_result():
    store = NPCProfileStore(":memory:")
    existing_id = store.upsert(
        NPCProfile(
            name="裴玉奴",
            public_summary="鸣玉坊琵琶名手。",
            last_known_location="鸣玉坊",
        )
    )
    existing = store.get_by_id(existing_id)
    scene_text = "裴玉奴如今为醉花楼献艺。"
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "existing",
                "matched_npc_index": 0,
                "persist_alias": False,
                "turn_observation": "裴玉奴在醉花楼弹了一曲。",
                "temporary_patch": {
                    "last_known_location": "醉花楼",
                    "current_intent": "为醉花楼献艺",
                },
                "long_term_patch": {
                    "role": "醉花楼琵琶名手",
                    "field_evidence": {
                        "role": ["turn_0_p001"],
                    },
                },
                "new_profile": None,
            }]
        }]
    )

    update_npc_profiles_from_extraction(
        scene_facts="裴玉奴如今为醉花楼献艺。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[existing],
        evidence_documents=_evidence_documents(scene_text),
        turn_index=0,
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="裴玉奴", context_span="裴玉奴为醉花楼献艺。")]
        ),
    )

    updated = store.get_by_id(existing_id)
    assert updated.last_known_location == "醉花楼"
    assert updated.current_intent == "为醉花楼献艺"
    assert updated.role == "醉花楼琵琶名手"


def test_new_resolution_strips_temporary_and_long_term_patches():
    store = NPCProfileStore(":memory:")
    gateway = _Gateway(
        [{
            "results": [{
                "candidate_index": 0,
                "resolution": "new",
                "matched_npc_index": None,
                "persist_alias": False,
                "turn_observation": None,
                "temporary_patch": {
                    "last_known_location": "醉花楼",
                },
                "long_term_patch": {
                    "role": "书生",
                    "field_evidence": {"role": ["turn_0_p001"]},
                },
                "new_profile": {
                    "name": "林川",
                    "aliases": [],
                    "public_summary": "来自白鹤书院的年轻书生。",
                    "role": "白鹤书院书生",
                    "appearance": None,
                    "personality_trait": None,
                    "known_abilities": [],
                    "faction": "白鹤书院",
                    "last_known_location": "醉花楼",
                    "attitude_to_player": "友善",
                    "current_intent": "向萧云痕请教",
                },
            }]
        }]
    )

    result = update_npc_profiles_from_extraction(
        scene_facts="林川是来自白鹤书院的年轻书生。",
        current_state=_state(),
        core_memory=CoreMemory(items=[]),
        gateway=gateway,
        npc_store=store,
        existing_profiles=[],
        extraction=NPCExtractionResult(
            mentions=[NPCMention(name="林川", context_span="林川是白鹤书院书生。")]
        ),
    )

    assert result.saved_ids == []
    assert len(result.new_profiles) == 1
    assert result.new_profiles[0].role == "白鹤书院书生"
