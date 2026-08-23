import pytest

import json
from app.domain.core_memory import CoreMemory
from app.domain.current_state import WorldClock
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.director.generation import run_generation_director_agent
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork

from app.tests.factories.director import (
    ScriptedDirectorGateway,
    make_current_state,
    make_director_npc,
)


def test_generation_director_observes_setting_without_facts(tmp_path):
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
    observed_settings = observation["resources"]["settings"]
    assert {
        item["setting_id"] for item in observed_settings
    } == {setting.setting_id, unrelated.setting_id}
    assert all("public_facts" not in item for item in observed_settings)
    assert all("hidden_facts" not in item for item in observed_settings)


def test_generation_agent_observes_only_prepared_director_npcs(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    prepared = make_director_npc().model_copy(
        update={"draft_id": "director_npc_prepared"}
    )
    introduced = make_director_npc().model_copy(
        update={
            "draft_id": "director_npc_introduced",
            "name": "顾晚棠",
            "status": "introduced",
            "introduced_turn": 2,
        }
    )
    with stores.director_npc as store:
        store.save(prepared)
        store.save(introduced)
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
    observed_drafts = [
        item
        for item in observation["resources"]["drafts"]
        if item.get("source") == "director_npc"
    ]
    assert [item["npc_id"] for item in observed_drafts] == [
        "director_npc_prepared"
    ]
    assert "director_npc_introduced" not in {
        item["npc_id"] for item in observation["resources"]["drafts"]
    }
    assert "director_npc_introduced" not in {
        item["npc_id"] for item in observation["resources"]["npcs"]
    }


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


def test_generation_agent_observation_includes_recent_director_log(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    from app.domain.world_setting import WorldSetting
    with stores.world_setting as store:
        store.save(WorldSetting(
            kind="lore",
            name="潮汐旧约",
            summary="约束沿海商会共同救援遇险船只的古老约定。",
            public_facts=["拒绝救援会被同行抵制"],
            created_turn=1,
        ))
    with stores.director_log as log_store:
        log_store.save_entries([{
            "turn_index": 2,
            "phase": "confirmation",
            "reasoning": "确认端创造了事件。",
            "measures": "创建事件(arc) 演员0",
        }])
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
    assert "recent_director_log" not in observation
    log_payload = json.loads(
        gateway.input_data[0]["recent_director_log_json"]
    )
    assert log_payload[0]["turn_index"] == 2
    assert log_payload[0]["phase"] == "confirmation"
    assert "创建事件" in log_payload[0]["measures"]


def test_generation_agent_stages_existing_npc_without_mutating_it(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft = make_director_npc()
    with stores.director_npc as store:
        store.save(draft)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "社区医生与本轮地点直接相关。",
            "requests": [
                {
                    "request_id": "npc_1",
                    "skill": "introduce_npc",
                    "arguments": {
                        "npc_id": draft.draft_id,
                        "view_mode": "remote",
                    },
                    "guidance": "通过第三方转述体现她克制的说话方式。",
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

    assert decision.requests[0].skill == "introduce_npc"
    assert stage.materials[0].resource_id == draft.draft_id
    assert stage.materials[0].guidance == "通过第三方转述体现她克制的说话方式。"
    assert stage.materials[0].payload["source"] == "director_npc"
    assert stage.materials[0].payload["name"] == "林澈"
    assert stage.materials[0].payload["social_style"] == "克制而友善。"
    assert "hidden_secret" not in stage.materials[0].payload
    assert "core_motivation" not in stage.materials[0].payload
    assert "appearance" not in stage.materials[0].payload
    assert "draft_id" in stage.materials[0].payload
    with stores.director_npc as store:
        assert store.get_by_id(draft.draft_id).status == "prepared"


def test_generation_agent_remote_view_filters_nonexistent_fields_for_director_npc(
    tmp_path,
):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft = make_director_npc()
    with stores.director_npc as store:
        store.save(draft)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "通过转述体现人物。",
            "requests": [
                {
                    "request_id": "npc_1",
                    "skill": "introduce_npc",
                    "arguments": {
                        "npc_id": draft.draft_id,
                        "view_mode": "remote",
                    },
                    "guidance": "通过转述体现人物。",
                }
            ],
        }
    )

    stage, _ = run_generation_director_agent(
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

    assert stage.materials[0].payload["source"] == "director_npc"
    assert "aliases" not in stage.materials[0].payload
    assert "public_summary" not in stage.materials[0].payload
    assert "attitude_to_player" not in stage.materials[0].payload
    assert stage.materials[0].payload["role"] == "社区医生"
    assert stage.materials[0].payload["social_style"] == "克制而友善。"


def test_generation_agent_intimate_view_reveals_director_npc_secrets(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft = make_director_npc()
    with stores.director_npc as store:
        store.save(draft)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "深度交心场景。",
            "requests": [
                {
                    "request_id": "npc_1",
                    "skill": "introduce_npc",
                    "arguments": {
                        "npc_id": draft.draft_id,
                        "view_mode": "intimate",
                    },
                    "guidance": "通过搜魂场景揭示其隐藏秘密。",
                }
            ],
        }
    )

    stage, _ = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="对林澈施展搜魂术。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
    )

    assert stage.materials[0].payload["source"] == "director_npc"
    assert stage.materials[0].payload["core_motivation"] == "保护社区居民。"
    assert stage.materials[0].payload["hidden_secret"] == "曾目击一宗未结案件。"
    assert stage.materials[0].payload["appearance"] == "常穿深色风衣。"


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


def test_generation_agent_receives_projected_turn_resolution(tmp_path):
    import json

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "本轮无额外舞台调度。",
            "requests": [],
        }
    )
    turn_resolution_json = json.dumps(
        {
            "action_resolutions": [
                {
                    "action_id": "a_1",
                    "player_action": "向码头工人打听沉船的事。",
                    "outcome": "success",
                    "targets": [],
                    "intent_match": True,
                }
            ]
        },
        ensure_ascii=False,
    )

    from app.domain.world_setting import WorldSetting
    with stores.world_setting as store:
        store.save(WorldSetting(
            kind="lore",
            name="潮汐旧约",
            summary="约束沿海商会共同救援遇险船只的古老约定。",
            public_facts=["拒绝救援会被同行抵制"],
            created_turn=1,
        ))

    _, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="向码头工人打听沉船的事。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        turn_resolution_json=turn_resolution_json,
    )

    assert decision.requests == []
    decide_input = gateway.input_data[0]["director_observation_json"]
    observation = json.loads(decide_input)
    assert observation["turn_resolution"]["action_resolutions"][0]["player_action"] == (
        "向码头工人打听沉船的事。"
    )
    assert "action_resolutions" in observation["turn_resolution"]


def test_generation_agent_projects_pending_engagement_without_writing(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft = make_director_npc()
    with stores.director_npc as store:
        store.save(draft)
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(
        role="ambient",
        turn_index=1,
        current_clock=WorldClock(),
    )
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "玩家深夜赴码头，接触水面摇橹声。",
            "requests": [
                {
                    "request_id": "npc_1",
                    "skill": "introduce_npc",
                    "arguments": {
                        "npc_id": draft.draft_id,
                        "view_mode": "face_to_face",
                    },
                    "guidance": "当面呈现码头猎户。",
                }
            ],
        }
    )

    stage, _ = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="深夜去旧码头看看声音来源。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        touched_event_candidates=[event_id],
    )

    assert stage.materials[0].kind == "npc"
    assert stage.materials[0].payload.get("source") == "director_npc"
    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"
    with stores.director_npc as store:
        assert store.get_by_id(draft.draft_id).status == "prepared"


def test_generation_observation_exposes_engaged_events_channel(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    engaged = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    hidden = DirectorEventSelectionResult(
        current_story_direction="镇上传出流言。",
        reason="独立世界事件。",
        title="旧巷传闻",
        duration_phases=5,
        actor_roles=[],
        subject="镇民",
        intent="散布流言",
        method="茶楼闲谈",
        macro_intent="转移视线。",
        hook_pool=[
            EventHook(
                hook_id="hook_rumor",
                kind="rumor",
                content="茶楼有人说巷口夜里有响动。",
                trigger_anchors=["茶楼"],
            )
        ],
        world_impact=WorldImpact(description="镇民人心浮动。"),
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    just_engaged = hidden.model_copy(
        update={"event_id": "director_event_just_engaged"}
    )
    with stores.director_event as store:
        engaged_id = store.save(engaged)
        store.set_stage(engaged_id, "engaged", turn_index=2)
        hidden_id = store.save(hidden)
        just_engaged_id = store.save(just_engaged)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []}
    )
    run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="观察码头。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        touched_event_candidates=[just_engaged_id],
    )

    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    hidden_ids = {
        item["event_id"] for item in observation["resources"]["events"]
    }
    assert hidden_ids == {hidden_id}
    engaged_channel = {
        item["event_id"]: item
        for item in observation["resources"]["engaged_events"]
    }
    assert set(engaged_channel) == {engaged_id, just_engaged_id}
    assert engaged_channel[engaged_id]["stage"] == "engaged"
    assert engaged_channel[engaged_id]["touched_this_turn"] is False
    assert engaged_channel[engaged_id]["world_impact"] == {
        "description": "外围变为凡人活动区。"
    }
    assert engaged_channel[engaged_id]["macro_intent"] == "猎户为了生存不择手段。"
    assert engaged_channel[just_engaged_id]["stage"] == "engaged"
    assert engaged_channel[just_engaged_id]["touched_this_turn"] is True
    assert "hooks" not in engaged_channel[engaged_id]


def test_generation_director_omits_event_skills_when_no_events(tmp_path):
    from app.domain.world_setting import WorldSetting
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.world_setting as store:
        store.save(WorldSetting(
            kind="lore",
            name="潮汐旧约",
            summary="约束沿海商会共同救援遇险船只的古老约定。",
            public_facts=["拒绝救援会被同行抵制"],
            created_turn=1,
        ))
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

    skills = json.loads(gateway.input_data[0]["available_skills_json"])
    skill_names = {skill["name"] for skill in skills}
    assert "present_event" not in skill_names
    assert "intrude_engaged_event" not in skill_names
    assert "upgrade_disclosure" not in skill_names
    # introduce_npc is always available because it supports spontaneous NPC creation
    assert "introduce_npc" in skill_names
    assert "foreshadow_setting" in skill_names


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


def test_generation_director_introduce_spontaneous_npc_batch_duplicate_rejected(tmp_path):
    from app.domain.director.generation import _validate_unique_introduce_npc_targets
    from app.domain.director.skill_specs import IntroduceNPCArguments, SpontaneousNPCSpec

    # 1. Direct batch validator test
    args1 = IntroduceNPCArguments(
        spontaneous=SpontaneousNPCSpec(
            name="严铁匠",
            role="铸灵匠人",
            appearance="壮汉",
            personality_trait="暴躁",
        ),
        view_mode="face_to_face",
    )
    args2 = IntroduceNPCArguments(
        spontaneous=SpontaneousNPCSpec(
            name=" 严铁匠 ",
            role="同批次重复角色",
            appearance="另一个壮汉",
            personality_trait="冷漠",
        ),
        view_mode="face_to_face",
    )
    with pytest.raises(ValueError, match="is duplicated within the same decision batch"):
        _validate_unique_introduce_npc_targets([args1, args2])

    # 2. Integration test via scripted director gateway
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "同一次决策中下发两个同名即兴角色。",
            "requests": [
                {
                    "request_id": "req-1",
                    "skill": "introduce_npc",
                    "arguments": {
                        "spontaneous": {
                            "name": "严铁匠",
                            "role": "铸灵匠人",
                            "appearance": "壮汉",
                            "personality_trait": "暴躁",
                        },
                        "view_mode": "face_to_face",
                    },
                    "guidance": "出场打铁。",
                },
                {
                    "request_id": "req-2",
                    "skill": "introduce_npc",
                    "arguments": {
                        "spontaneous": {
                            "name": "严铁匠",
                            "role": "同批次重复角色",
                            "appearance": "另一个壮汉",
                            "personality_trait": "冷漠",
                        },
                        "view_mode": "face_to_face",
                    },
                    "guidance": "重复出场。",
                },
            ],
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

    # Batch conflict causes scripted validation failure; fallback with 0 materials
    assert len(stage.materials) == 0


def test_generation_director_introduce_spontaneous_npc_alias_collision_rejected(tmp_path):
    from app.domain.npc_profile import NPCProfile
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile as store:
        store.upsert(NPCProfile(
            name="老周",
            aliases=["周行之", "周掌柜"],
            transient_aliases={"行之兄": 5},
            role="商会执事",
            appearance="中年男子",
            personality_trait="精明",
            disclosure_level="face_to_face",
        ))

    # Test permanent alias collision
    gateway_perm = ScriptedDirectorGateway(
        decision={
            "reasoning": "即兴引入与别名同名角色。",
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
    stage, _ = run_generation_director_agent(
        gateway=gateway_perm,
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
    assert len(stage.materials) == 0

    # Test transient alias collision
    gateway_trans = ScriptedDirectorGateway(
        decision={
            "reasoning": "即兴引入与临时别名同名角色。",
            "requests": [{
                "request_id": "req-2",
                "skill": "introduce_npc",
                "arguments": {
                    "spontaneous": {
                        "name": "行之兄",
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
    stage_trans, _ = run_generation_director_agent(
        gateway=gateway_trans,
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
    assert len(stage_trans.materials) == 0


def test_generation_director_introduce_spontaneous_npc_protagonist_collision_rejected(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "即兴引入与主角同名的角色。",
            "requests": [{
                "request_id": "req-1",
                "skill": "introduce_npc",
                "arguments": {
                    "spontaneous": {
                        "name": "萧欢",
                        "role": "同名假身",
                        "appearance": "特征描述",
                        "personality_trait": "性格描述",
                    },
                    "view_mode": "face_to_face",
                },
                "guidance": "出现并打招呼。",
            }],
        },
    )

    state = make_current_state()
    state.player.name = "萧欢"

    stage, _ = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=state,
        user_input="进店。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
    )
    assert len(stage.materials) == 0
