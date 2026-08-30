import pytest

import json
from pydantic import ValidationError
from app.domain.core_memory import CoreMemory
from app.domain.current_state import WorldClock
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.director.confirmation import DirectorConfirmationExecutor
from app.domain.director.generation import run_generation_director_agent
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import make_current_state
from app.tests.factories.director import make_event_selection_output
from app.tests.factories.director import make_intrude_event


def test_confirmation_agent_can_create_event_with_new_actors(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="盐雾港",
        summary="依靠潮汐盐田与近海货运生存的港镇。",
        public_facts=["港民熟悉潮汐航道"],
        hidden_facts=["旧盐仓下有走私暗渠"],
        created_turn=1,
    )
    with stores.world_setting as store:
        store.save(setting)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "需要同时孕育一个事件与它的核心班底。",
            "requests": [
                {
                    "request_id": "event_1",
                    "skill": "create_event",
                    "arguments": {
                        "role": "arc",
                        "setting_requirements": [{
                            "kind": "location",
                            "setting_description": "事件依托该港镇的生态。",
                            "matched_setting_id": setting.setting_id,
                        }],
                        "actors": [
                            {"source": "new", "guidance": "码头把头，世代守着货路。"},
                            {"source": "new", "guidance": "账房先生，暗中替世家管账。"},
                        ],
                    },
                    "guidance": "以码头货运世家为核心演员班底。",
                }
            ],
        },
        event_selection_output=make_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "控制码头货路"},
            {"actor_key": "actor_2", "role_in_event": "管理隐秘账目"},
        ]),
        npc_output={
            "creation_reason": "码头上的一等把式。",
            "identity": {
                "core_motivation": "守住码头的货路。",
                "hidden_secret": "当年沉船时他就在船上。",
                "role": "码头把头",
                "faction": "码头会",
            },
            "behavior": {
                "conflict_style": "先硬扛再周旋。",
                "social_style": "话少，办事利落。",
                "personality_trait": "认人不认账，帮过的伙计记一辈子",
            },
            "shell": {
                "known_abilities": [
                    {"name": "盘货", "description": "凭手感辨认船货成色"},
                    {"name": "相人", "description": "看一个人几眼就能判断靠不靠得住"},
                ],
                "appearance": "左臂有一道旧疤。",
                "name": "沈二爷",
            },
        },
    )

    decision, executions = DirectorConfirmationExecutor(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
    ).run()

    assert executions.executions[0].skill == "create_event"
    output = executions.executions[0].output
    assert output["role"] == "arc"
    assert len(output["new_npc_ids"]) == 2
    assert len(output["actor_names"]) == 2
    assert gateway.tasks == [
        "director_agent_decide_confirmation",
        "director_event_select_arc",
        "director_npc_create",
        "director_npc_create",
    ]

    with stores.director_event as store:
        events = store.list_all()
        assert [event.title for event in events] == ["沉船账本"]
        bound = store.get_by_id(output["event_id"])
        assert bound is not None
        assert bound.deadline_clock == WorldClock(day=2, phase="清晨")
        assert {(actor.ref.kind, actor.ref.resource_id) for actor in bound.actors if actor.ref} == {
            ("npc", npc_id) for npc_id in output["new_npc_ids"]
        }
        assert set(output["actor_names"]) <= {actor.name for actor in bound.actors}
    with stores.director_npc as store:
        prepared = store.list_prepared()
        assert {draft.draft_id for draft in prepared} == set(output["new_npc_ids"])

    create_calls = [
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_npc_create"
    ]
    assert len(create_calls) == 2
    first = create_calls[0]["batch_recent_creations_json"]
    assert first.count("沉船账本") == 1
    assert "沈二爷" not in first
    second = create_calls[1]["batch_recent_creations_json"]
    assert second.count("沉船账本") == 1
    assert "沈二爷" in second
    assert "actor_roles" in second
    assert "控制码头货路" in second
    assert "管理隐秘账目" in second
    assert "码头把头，世代守着货路。" in create_calls[0]["director_guidance"]
    assert "控制码头货路" in create_calls[0]["director_guidance"]
    assert "账房先生，暗中替世家管账。" in create_calls[1]["director_guidance"]
    assert "管理隐秘账目" in create_calls[1]["director_guidance"]

    selector_input = [
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_event_select_arc"
    ]
    assert selector_input[0]["director_guidance"] == "以码头货运世家为核心演员班底。"


def test_confirmation_agent_can_mix_existing_and_new_event_actors(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="盐雾港",
        summary="依靠潮汐盐田与近海货运生存的港镇。",
        public_facts=["港民熟悉潮汐航道"],
        hidden_facts=["旧盐仓下有走私暗渠"],
        created_turn=1,
    )
    with stores.world_setting as store:
        store.save(setting)
    with stores.npc_profile as npc_store:
        existing_id = npc_store.upsert(
            NPCProfile(
                name="陆远舟",
                role="镖师",
                core_motivation="守住商路信誉。",
                hidden_secret="曾替码头世家运过密货。",
            )
        )
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "已有镖师会雇用一个新的执行者。",
            "requests": [{
                "request_id": "event_1",
                "skill": "create_event",
                "arguments": {
                    "role": "arc",
                    "setting_requirements": [{
                        "kind": "location",
                        "setting_description": "事件依托该港镇的生态。",
                        "matched_setting_id": setting.setting_id,
                    }],
                    "actors": [
                        {
                            "source": "existing",
                            "npc_id": existing_id,
                            "guidance": "作为幕后委托者，不改变既有动机。",
                        },
                        {
                            "source": "new",
                            "guidance": "受雇调查沉船的新面孔。",
                        },
                    ],
                },
                "guidance": "让新旧演员共同推动事件。",
            }],
        },
        event_selection_output=make_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "提供目标与资源"},
            {"actor_key": "actor_2", "role_in_event": "执行沉船调查"},
        ]),
        npc_output={
            "creation_reason": "沉船事件需要一名现场调查者。",
            "identity": {
                "core_motivation": "靠查明沉船真相获得立足之地。",
                "hidden_secret": "曾参与伪造一份船货清单。",
                "role": "沉船调查者",
                "faction": None,
            },
            "behavior": {
                "conflict_style": "先观察再突然行动。",
                "social_style": "谨慎而直接。",
                "personality_trait": "下结论前先蹲下来把现场看了又看",
            },
            "shell": {
                "known_abilities": [
                    {"name": "潜水", "description": "潜到沉船残骸中捞取物证"},
                    {"name": "辨认船货", "description": "凭痕迹辨认船货来源与去向"},
                ],
                "appearance": "总穿着防水短衣。",
                "name": "闻潮",
            },
        },
    )

    _, executions = DirectorConfirmationExecutor(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
    ).run()

    output = executions.executions[0].output
    assert len(output["new_npc_ids"]) == 1
    with stores.director_event as event_store:
        event = event_store.get_by_id(output["event_id"])
    assert event is not None
    assert {actor.name for actor in event.actors} == {"陆远舟", "闻潮"}
    assert {actor.ref.resource_id for actor in event.actors if actor.ref} == {
        existing_id,
        output["new_npc_ids"][0],
    }
    selector_input = next(
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_event_select_arc"
    )
    required = json.loads(selector_input["required_actors_json"])
    assert required[0]["actor_key"] == "actor_1"
    assert required[0]["profile"]["name"] == "陆远舟"
    assert required[0]["profile"]["hidden_secret"] == "曾替码头世家运过密货。"
    assert required[1] == {
        "actor_key": "actor_2",
        "source": "new",
        "guidance": "受雇调查沉船的新面孔。",
    }


def test_create_event_rejects_missing_required_actor_role(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="盐雾港",
        summary="依靠潮汐盐田与近海货运生存的港镇。",
        public_facts=["港民熟悉潮汐航道"],
        hidden_facts=["旧盐仓下有走私暗渠"],
        created_turn=1,
    )
    with stores.world_setting as store:
        store.save(setting)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "创建带新演员的事件。",
            "requests": [{
                "request_id": "event_1",
                "skill": "create_event",
                "arguments": {
                    "role": "ambient",
                    "setting_requirements": [{
                        "kind": "location",
                        "setting_description": "事件依托该港镇的生态。",
                        "matched_setting_id": setting.setting_id,
                    }],
                    "actors": [{"source": "new", "guidance": "码头巡夜人。"}],
                },
                "guidance": "围绕码头夜巡建立事件。",
            }],
        },
        event_selection_output=make_event_selection_output(),
    )

    with pytest.raises(
        ValueError,
        match="actor_roles must cover exactly every required actor_key",
    ):
        DirectorConfirmationExecutor(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=make_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            turn_index=3,
            trace_id="confirmation-director",
        ).run()


def test_create_event_rejects_unknown_existing_actor_before_selection(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="盐雾港",
        summary="依靠潮汐盐田与近海货运生存的港镇。",
        public_facts=["港民熟悉潮汐航道"],
        hidden_facts=["旧盐仓下有走私暗渠"],
        created_turn=1,
    )
    with stores.world_setting as store:
        store.save(setting)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "错误地引用不存在的演员。",
            "requests": [{
                "request_id": "event_1",
                "skill": "create_event",
                "arguments": {
                    "role": "arc",
                    "setting_requirements": [{
                        "kind": "location",
                        "setting_description": "事件依托该港镇的生态。",
                        "matched_setting_id": setting.setting_id,
                    }],
                    "actors": [{
                        "source": "existing",
                        "npc_id": "npc_missing",
                        "guidance": "作为幕后行动者。",
                    }],
                },
                "guidance": "建立幕后事件。",
            }],
        },
        event_selection_output=make_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "幕后行动"},
        ]),
    )

    with pytest.raises(ValueError, match="unknown active NPC"):
        DirectorConfirmationExecutor(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=make_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            turn_index=3,
            trace_id="confirmation-director",
        ).run()
    assert gateway.tasks == ["director_agent_decide_confirmation"]


def test_wave_b_failure_rolls_back_wave_a_settings_and_all_writes(
    tmp_path, monkeypatch
):
    from app.domain.director_npc.creator import DirectorNPCCreator

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "同批一个 NPC 和一个事件都缺 Setting，事件侧演员创建失败。",
            "requests": [
                {
                    "request_id": "npc_1",
                    "skill": "create_npc",
                    "arguments": {
                        "setting_requirements": [{
                            "kind": "faction",
                            "setting_description": "agent 需要的北境势力。",
                        }],
                    },
                    "guidance": "凭空补建北境势力。",
                },
                {
                    "request_id": "event_1",
                    "skill": "create_event",
                    "arguments": {
                        "role": "ambient",
                        "setting_requirements": [{
                            "kind": "location",
                            "setting_description": "agent 需要的交通节点。",
                        }],
                        "actors": [{"source": "new", "guidance": "码头巡夜人。"}],
                    },
                    "guidance": "建立独立环境事件。",
                },
            ],
        },
        event_selection_output=make_event_selection_output(),
        setting_output={
            "name": "寒川驿",
            "summary": "连接北境山道和河运的交通节点。",
            "public_facts": ["商旅会在此更换车马。"],
            "hidden_facts": ["地下仓道通往废弃河港。"],
        },
        npc_output={
            "creation_reason": "北境商路需要一个长期活动的人物。",
            "identity": {
                "core_motivation": "守住商队赖以生存的货路。",
                "hidden_secret": "私下追查一批失踪货物。",
                "role": "北境行商",
                "faction": "北陆商盟",
            },
            "behavior": {
                "conflict_style": "先谈条件，再切断补给。",
                "social_style": "言简意赅，重视信用。",
                "personality_trait": "报价前先捻三遍算珠，算不清就不松口",
            },
            "shell": {
                "known_abilities": [{"name": "估价", "description": "凭成色与行情估算货物价值"}],
                "appearance": "常穿防风皮袍。",
                "name": "沈栖舟",
            },
        },
    )

    def flaky_create(self, **kwargs):
        raise TimeoutError("LLM 超时")

    monkeypatch.setattr(DirectorNPCCreator, "create", flaky_create)

    with pytest.raises(TimeoutError, match="LLM 超时"):
        DirectorConfirmationExecutor(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=make_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            turn_index=3,
            trace_id="confirmation-director",
        ).run()

    with stores.world_setting as store:
        assert store.list_all() == []
    with stores.director_npc as store:
        assert store.list_all() == []
    with stores.director_event as store:
        assert store.list_all() == []


def test_create_event_uses_request_guidance_as_event_direction(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="河岸船坞",
        summary="承担沿河商船修造与维护的老船坞。",
        public_facts=["船坞由几个老师傅共同经营"],
        hidden_facts=["部分旧账从未对外公开"],
        created_turn=1,
    )
    unrelated = setting.model_copy(update={
        "setting_id": "setting_unrelated",
        "name": "北岭矿场",
        "summary": "为内陆城镇供应铁矿的露天矿场。",
    })
    with stores.world_setting as store:
        store.save(setting)
        store.save(unrelated)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "需要孕育事件班底。",
            "requests": [
                {
                    "request_id": "event_1",
                    "skill": "create_event",
                    "arguments": {
                        "role": "ambient",
                        "actors": [],
                        "setting_requirements": [{
                            "kind": "location",
                            "setting_description": "事件依托该港镇的生态。",
                            "matched_setting_id": setting.setting_id,
                        }],
                    },
                    "guidance": "以船坞翻修风波为整体方向。",
                }
            ],
        },
        event_selection_output=make_event_selection_output(),
    )

    _, executions = DirectorConfirmationExecutor(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
    ).run()

    assert executions.executions[0].skill == "create_event"
    selector_input = [
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_event_select_ambient"
    ]
    assert selector_input[0]["director_guidance"] == "以船坞翻修风波为整体方向。"
    observation = json.loads(gateway.input_data[0]["director_observation_json"])
    observed_setting = next(
        item
        for item in observation["resources"]["settings"]
        if item["setting_id"] == setting.setting_id
    )
    assert observed_setting["event_creation_available"] is True
    [selected_setting] = json.loads(selector_input[0]["settings_json"])
    assert selected_setting["name"] == setting.name
    assert selected_setting["public_facts"] == [{
        "fact_id": "fact_1",
        "content": setting.public_facts[0],
    }]
    assert selected_setting["hidden_facts"] == [{
        "fact_id": "fact_2",
        "content": setting.hidden_facts[0],
    }]
    assert unrelated.name not in selector_input[0]["settings_json"]
    assert "director_npc_create" not in gateway.tasks
    with stores.director_event as store:
        [saved] = store.list_all()
    assert saved.source_setting_ids == [setting.setting_id]
    assert saved.source_setting_fact_ids == ["fact_1"]


def test_event_selector_omits_and_rejects_previously_used_setting_fact_id():
    from app.domain.director_event.selector import DirectorEventSelector
    from app.domain.world_setting import WorldSetting

    setting = WorldSetting(
        kind="location",
        name="河岸船坞",
        summary="承担沿河商船修造与维护的老船坞。",
        public_facts=["船坞由几个老师傅共同经营"],
        hidden_facts=["部分旧账从未对外公开"],
        created_turn=1,
    )
    previous = make_intrude_event()
    previous = previous.model_copy(update={
        "stage": "resolved",
        "resolved_reason": "猎户已经撤离，码头恢复通行。",
        "final_world_impact": previous.world_impact,
        "source_setting_ids": [setting.setting_id],
        "source_setting_fact_ids": ["fact_1"],
    })
    available_output = make_event_selection_output()
    available_output["selected_setting_fact_ids"] = ["fact_2"]
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "unused", "requests": []},
        event_selection_output=available_output,
    )

    selection = DirectorEventSelector(gateway).select_or_create(
        role="ambient",
        events=[previous],
        current_state=make_current_state(),
        core_memory=CoreMemory(items=[]),
        settings=[setting],
    )

    [selected_setting] = json.loads(gateway.input_data[0]["settings_json"])
    [previous_event] = json.loads(gateway.input_data[0]["events_json"])
    assert previous_event["resolved_reason"] == "猎户已经撤离，码头恢复通行。"
    assert previous_event["final_world_impact"] == (
        previous.final_world_impact.model_dump(mode="json")
    )
    assert selected_setting["public_facts"] == [{
        "content": setting.public_facts[0],
    }]
    assert "fact_id" not in selected_setting["public_facts"][0]
    assert selected_setting["hidden_facts"][0]["fact_id"] == "fact_2"
    assert selection.selected_setting_fact_ids == ["fact_2"]

    rejected_gateway = ScriptedDirectorGateway(
        decision={"reasoning": "unused", "requests": []},
        event_selection_output=make_event_selection_output(),
    )
    with pytest.raises(ValueError, match="unavailable"):
        DirectorEventSelector(rejected_gateway).select_or_create(
            role="ambient",
            events=[previous],
            current_state=make_current_state(),
            core_memory=CoreMemory(items=[]),
            settings=[setting],
        )


def test_confirmation_rejects_exhausted_event_setting(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="河岸船坞",
        summary="承担沿河商船修造与维护的老船坞。",
        public_facts=["船坞由几个老师傅共同经营"],
        hidden_facts=["部分旧账从未对外公开"],
        created_turn=1,
    )
    with stores.world_setting as store:
        store.save(setting)
    event = make_intrude_event().model_copy(update={
        "source_setting_ids": [setting.setting_id],
        "source_setting_fact_ids": ["fact_1", "fact_2"],
    })
    with stores.director_event as store:
        event_id = store.save(event)
        store.resolve_event(
            event_id,
            turn_index=2,
            resolved_reason="事件已经收束。",
        )
    gateway = ScriptedDirectorGateway(decision={
        "reasoning": "继续沿用船坞。",
        "requests": [{
            "request_id": "event_1",
            "skill": "create_event",
            "arguments": {
                "role": "ambient",
                "setting_requirements": [{
                    "kind": "location",
                    "setting_description": "继续使用河岸船坞。",
                    "matched_setting_id": setting.setting_id,
                }],
                "actors": [],
            },
            "guidance": "继续使用河岸船坞。",
        }],
    })

    decision, executions = DirectorConfirmationExecutor(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="继续前进。",
        scene_facts={"scene_focus": "旧事件已经结束。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
    ).run()

    observation = json.loads(gateway.input_data[0]["director_observation_json"])
    [observed_setting] = observation["resources"]["settings"]
    assert observed_setting["event_creation_available"] is False
    skills = json.loads(gateway.input_data[0]["available_skills_json"])
    assert "create_event" in {skill["name"] for skill in skills}
    assert decision.requests == []
    assert executions.executions == []
    assert "director_event_select_ambient" not in gateway.tasks


def test_create_event_rejects_more_than_three_new_actors():
    from app.domain.director.skill_specs import CreateEventArguments

    with pytest.raises(ValidationError, match="at most 3 new actors"):
        CreateEventArguments(
            role="arc",
            setting_requirements=[{
                "kind": "location",
                "setting_description": "事件依托的既有土壤。",
                "matched_setting_id": "setting_1",
            }],
            actors=[
                {"source": "new", "guidance": f"方向{index}"}
                for index in range(4)
            ],
        )


def test_director_event_semantic_view_hides_internal_state(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult

    selection = DirectorEventSelectionResult.model_validate(make_event_selection_output())
    event = selection.to_event(
        role="arc", turn_index=3, current_clock=WorldClock()
    )
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_event as store:
        store.save(event)
        saved = store.get_by_id(event.event_id)
    assert saved is not None
    assert saved.hook_pool
    assert saved.world_impact is not None
    assert "hook_pool" not in saved.semantic_view()
    assert "world_impact" not in saved.semantic_view()


def test_present_event_rejects_engaged_event(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
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
        store.set_stage(event_id, "engaged", turn_index=2)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "尝试重复呈现已卷入事件。",
            "requests": [
                {
                    "request_id": "evt_1",
                    "skill": "present_event",
                    "arguments": {
                        "event_id": event_id,
                        "hook_id": "hook_sound",
                    },
                    "guidance": "作为背景舞台提示呈现。",
                }
            ],
        }
    )

    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="继续在码头巡查。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        touched_event_candidates=[],
    )

    assert decision.requests == []
    assert stage.materials == []


def test_intrude_engaged_event_rejects_hidden_resolved_and_unknown(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    hidden = make_intrude_event()
    with stores.director_event as store:
        hidden_id = store.save(hidden)
        engaged_id = store.save(make_intrude_event().model_copy(
            update={"event_id": "director_event_engaged_intrude"}
        ))
        store.set_stage(engaged_id, "engaged", turn_index=2)
        resolved_id = store.save(make_intrude_event().model_copy(
            update={"event_id": "director_event_resolved_intrude"}
        ))
        store.resolve_event(
            resolved_id,
            turn_index=3,
            resolved_reason="测试结算。",
            final_world_impact=None,
        )

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "测试非法调度。",
            "requests": [
                {
                    "request_id": "m1",
                    "skill": "intrude_engaged_event",
                    "arguments": {"event_id": hidden_id},
                    "guidance": "将事件前景化。",
                }
            ],
        }
    )
    def assert_empty_decision(gateway):
        stage, decision = run_generation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=make_current_state(),
            user_input="巡查码头。",
            context_facts=[],
            recent_summaries=[],
            turn_index=3,
            trace_id="generation-director",
            npc_profiles=[],
            touched_event_candidates=[],
        )
        assert decision.requests == []
        assert stage.materials == []

    assert_empty_decision(gateway)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "测试非法调度。",
            "requests": [
                {
                    "request_id": "m1",
                    "skill": "intrude_engaged_event",
                    "arguments": {"event_id": resolved_id},
                    "guidance": "将事件前景化。",
                }
            ],
        }
    )
    assert_empty_decision(gateway)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "测试未知事件。",
            "requests": [
                {
                    "request_id": "m1",
                    "skill": "intrude_engaged_event",
                    "arguments": {"event_id": "director_event_missing"},
                    "guidance": "将事件前景化。",
                }
            ],
        }
    )
    assert_empty_decision(gateway)


def test_intrude_engaged_event_produces_director_material(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event = make_intrude_event()
    with stores.director_event as store:
        event_id = store.save(event)
        store.set_stage(event_id, "engaged", turn_index=2)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "调度商路封锁事件。",
            "requests": [
                {
                    "request_id": "m1",
                    "skill": "intrude_engaged_event",
                    "arguments": {"event_id": event_id},
                    "guidance": (
                        "将商路封锁作为本回合的实质干涉：关卡盘查在必经之路设卡，"
                        "拦下过路商队逐一搜身，主角被迫决定绕路还是上前应对。"
                    ),
                }
            ],
        }
    )

    stage, _decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="巡查码头。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        touched_event_candidates=[],
    )

    assert len(stage.materials) == 1
    material = stage.materials[0]
    assert material.kind == "engaged_event"
    assert material.resource_id == event_id
    assert "实质干涉" in material.guidance
    assert "被迫决定" in material.guidance
    assert material.payload["stage"] == "engaged"
    assert material.payload["macro_intent"] == "改变商路格局。"
    assert "deadline_clock" in material.payload
    assert "world_impact" not in material.payload


def test_intrude_engaged_event_allows_just_engaged_pending_event(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_event as store:
        event_id = store.save(make_intrude_event())

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "玩家本回合新卷入，需要调度。",
            "requests": [
                {
                    "request_id": "m1",
                    "skill": "intrude_engaged_event",
                    "arguments": {"event_id": event_id},
                    "guidance": "事件作为本回合前景推进。",
                }
            ],
        }
    )

    stage, _decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="主动调查无灯船。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        touched_event_candidates=[event_id],
    )

    assert len(stage.materials) == 1
    assert stage.materials[0].kind == "engaged_event"
    assert stage.materials[0].resource_id == event_id


def test_intrude_engaged_event_requires_guidance(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_event as store:
        event_id = store.save(make_intrude_event())
        store.set_stage(event_id, "engaged", turn_index=2)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "引入事件但未给指令。",
            "requests": [
                {
                    "request_id": "m1",
                    "skill": "intrude_engaged_event",
                    "arguments": {"event_id": event_id},
                    "guidance": "",
                }
            ],
        }
    )
    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="巡查码头。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        touched_event_candidates=[],
    )

    assert decision.requests == []
    assert stage.materials == []


def test_self_check_preserves_touched_event_ids():
    from app.domain.action.self_check import ActionSelfCheck
    from app.domain.action.schemas import ActionParseResult

    result = ActionParseResult(
        analysis="分析。",
        atomic_actions=[],
        anchors=[],
        touched_event_ids=["event_1", "event_2"],
    )
    checked = ActionSelfCheck().check(
        result,
        user_input="去码头看看。",
        current_state=make_current_state(),
    )
    assert checked.touched_event_ids == ["event_1", "event_2"]
