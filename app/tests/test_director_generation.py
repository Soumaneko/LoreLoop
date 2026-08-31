import pytest

import json
from app.domain.core_memory import CoreMemory
from app.domain.current_state import WorldClock
from app.domain.npc_profile.schemas import NPCProfile
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork

from app.tests.factories.director import (
    ScriptedDirectorGateway,
    make_current_state,
    make_director_npc,
    run_generation_director_agent,
)


def test_generation_director_no_longer_observes_settings(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="lore",
        name="潮汐旧约",
        summary="约束沿海商会共同救援遇险船只的古老约定。",
        public_facts=["拒绝救援会被同行抵制"],
        hidden_facts=["旧约最初用于掩护违禁货运"],
        created_turn=1,
    )
    unrelated = setting.model_copy(update={
        "setting_id": "setting_unrelated",
        "name": "山邮驿",
        "summary": "负责内陆山路信件转运的驿站。",
    })
    with stores.world_setting as store:
        store.save(setting)
        store.save(unrelated)
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []},
    )

    run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="观察港口。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
    )

    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    assert "settings" not in observation["resources"]




def test_generation_observation_npcs_carry_disclosure_level(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile as store:
        profile_id = store.upsert(NPCProfile(
            name="顾晚棠",
            role="阁主",
            core_motivation="查明失窃真相。",
            hidden_secret="暗中资助商会。",
            disclosure_level="face_to_face",
        ))
        selected_profile = store.get_by_id(profile_id)
    prepared = make_director_npc().model_copy(
        update={"draft_id": "director_npc_plain_draft"}
    )
    with stores.director_npc as store:
        store.save(prepared)
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []},
    )

    run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="观察港口。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[selected_profile],
    )

    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    resources = observation["resources"]
    profiles = {
        item["npc_id"]: item for item in resources["npcs"]
    }
    assert profiles[profile_id]["disclosure_level"] == "face_to_face"
    assert profiles[profile_id]["source"] == "npc_profile"
    assert all("disclosure_level" not in item for item in resources["drafts"])
    assert all(item.get("source") == "director_npc" for item in resources["drafts"])








def test_introduce_npc_rejects_regular_profiles(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile as store:
        profile_id = store.upsert(
            NPCProfile(
                name="林澈",
                role="社区医生",
                aliases=["林医生"],
                social_style="克制而友善。",
            )
        )
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "尝试引入已有档案。",
            "requests": [
                {
                    "request_id": "npc_1",
                    "skill": "introduce_npc",
                    "arguments": {
                        "npc_id": profile_id,
                        "view_mode": "remote",
                    },
                    "guidance": "通过转述体现人物。",
                }
            ],
        }
    )

    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="去诊所询问昨夜发生的事。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
    )

    assert decision.requests == []
    assert stage.materials == []








def test_generation_director_introduce_spontaneous_npc_success(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "玩家前往铁匠铺，需要现场引入一名铁匠NPC。",
            "requests": [{
                "request_id": "req-1",
                "skill": "introduce_npc",
                "arguments": {
                    "spontaneous": {
                        "name": "严铁匠",
                        "role": "陷空山脚下的铸灵匠人",
                        "appearance": "赤膊壮汉，臂有火纹烫伤，手持重锤",
                        "personality_trait": "脾气暴躁，视锤如命",
                        "known_abilities": [{
                            "name": "淬火锻灵",
                            "description": "精通低阶法宝与粗胚精炼",
                        }],
                    },
                    "view_mode": "face_to_face",
                },
                "guidance": "主角询问矿石或兵器时，他态度冷淡，自顾自打铁。",
            }],
        },
    )

    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="前往铁匠铺买把趁手短刀。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
    )

    assert len(stage.materials) == 1
    material = stage.materials[0]
    assert material.kind == "npc"
    assert material.resource_id.startswith("spontaneous_npc_")
    assert material.guidance == "主角询问矿石或兵器时，他态度冷淡，自顾自打铁。"
    assert material.payload["source"] == "spontaneous"
    assert material.payload["name"] == "严铁匠"
    assert material.payload["role"] == "陷空山脚下的铸灵匠人"
    assert material.payload["view_mode"] == "face_to_face"
    assert len(material.payload["known_abilities"]) == 1
    assert material.payload["known_abilities"][0]["name"] == "淬火锻灵"


def test_generation_director_introduce_spontaneous_npc_name_collision_rejected(tmp_path):
    from app.domain.npc_profile import NPCProfile
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile as store:
        store.upsert(NPCProfile(
            name="周行之",
            role="商会执事",
            appearance="中年男子",
            personality_trait="精明",
            disclosure_level="face_to_face",
        ))

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "即兴引入同名角色。",
            "requests": [{
                "request_id": "req-1",
                "skill": "introduce_npc",
                "arguments": {
                    "spontaneous": {
                        "name": "周行之",
                        "role": "同名假商人",
                        "appearance": "特征描述",
                        "personality_trait": "性格描述",
                    },
                    "view_mode": "face_to_face",
                },
                "guidance": "出现并打招呼。",
            }],
        },
    )

    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="进店。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
    )

    # Name collision causes validation failure; rejected from stage.materials
    assert len(stage.materials) == 0
