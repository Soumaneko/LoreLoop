import pytest
from copy import deepcopy

import json
from pydantic import ValidationError
from app.domain.core_memory import CoreMemory
from app.domain.current_state import WorldClock
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.director.confirmation import DirectorConfirmationExecutor
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import make_current_state
from app.tests.factories.director import make_event_selection_output
from app.tests.factories.director import make_intrude_event


@pytest.mark.parametrize("with_additional", [False, True])
def test_confirmation_agent_can_create_event_with_new_actors(tmp_path, with_additional):
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

    expected_count = 3 if with_additional else 2
    additional_role = "老周，码头船工，负责接收账册并安排转运。"
    gateway.event_selection_output["additional_npcs"] = ([{
        "actor_key": "extra_1", "role_in_event": additional_role,
    }] if with_additional else [])
    names = iter(["沈二爷", "钱先生", "老周"])
    template = gateway.npc_output

    def npc_output(_):
        output = deepcopy(template)
        output["shell"]["name"] = next(names)
        return output

    gateway.npc_output = npc_output

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
    assert len(output["new_npc_ids"]) == expected_count
    assert len(output["actor_names"]) == expected_count
    assert gateway.tasks == [
        "director_agent_decide_confirmation",
        "director_event_design",
        "director_npc_create",
        "director_npc_create",
    ] + (["director_npc_create"] if with_additional else []) + ["director_event_compose"]

    with stores.director_event as store:
        events = store.list_all()
        assert [event.title for event in events] == ["沉船账本"]
        bound = store.get_by_id(output["event_id"])
        assert bound is not None
        assert bound.deadline_clock == WorldClock(day=2, phase="清晨")
        assert bound.plan == gateway.event_selection_output["method"]
        assert [fact.fact for fact in bound.current_situation] == ["账房正在码头追查失踪账册。"]
        assert all(fact.fact_id.startswith("event_fact_") for fact in bound.current_situation)
        store.save(bound)
        assert store.get_by_id(bound.event_id).current_situation == bound.current_situation
        assert bound.semantic_view()["current_situation"][0]["fact_id"] == bound.current_situation[0].fact_id
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
    assert len(create_calls) == expected_count
    first = create_calls[0]["batch_recent_creations_json"]
    assert json.loads(first)[0]["conception"] == gateway.event_selection_output["conception"]
    assert "沈二爷" not in first
    second = create_calls[1]["batch_recent_creations_json"]
    assert json.loads(second)[0]["conception"] == gateway.event_selection_output["conception"]
    assert "沈二爷" in second
    assert "actor_roles" in second
    assert "控制码头货路" in second
    assert "管理隐秘账目" in second
    assert "码头把头，世代守着货路。" in create_calls[0]["director_guidance"]
    assert "控制码头货路" in create_calls[0]["director_guidance"]
    assert "账房先生，暗中替世家管账。" in create_calls[1]["director_guidance"]
    assert "管理隐秘账目" in create_calls[1]["director_guidance"]
    if with_additional:
        assert create_calls[2]["director_guidance"] == additional_role
        context = json.loads(create_calls[2]["batch_recent_creations_json"])
        assert context[0]["additional_npcs"] == gateway.event_selection_output["additional_npcs"]
        assert "钱先生" in create_calls[2]["batch_recent_creations_json"]
        assert output["actor_names"][-1] == "老周"

    selector_input = [
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_event_design"
    ]
    assert selector_input[0]["director_guidance"] == "以码头货运世家为核心演员班底。"








@pytest.mark.parametrize("fail_at", ["npc", "composition"])
def test_wave_b_failure_rolls_back_wave_a_settings_and_all_writes(
    tmp_path, monkeypatch, fail_at
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
        event_selection_output=make_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "码头巡夜"},
            {"actor_key": "actor_2", "role_in_event": "追查失踪货物"},
        ]),
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

    original_create = DirectorNPCCreator.create
    created_drafts = []
    gateway.event_selection_output["additional_npcs"] = [{
        "actor_key": "extra_1", "role_in_event": "码头船工，负责运走货物。",
    }]

    def flaky_create(self, **kwargs):
        if fail_at == "npc" and len(created_drafts) == 2:
            raise TimeoutError("LLM 超时")
        draft = original_create(self, **kwargs)
        created_drafts.append(draft)
        return draft

    monkeypatch.setattr(DirectorNPCCreator, "create", flaky_create)
    if fail_at == "composition":
        def fail_composition(_):
            raise TimeoutError("LLM 超时")
        gateway.event_composition_output = fail_composition

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

    assert len(created_drafts) == (2 if fail_at == "npc" else 3)
    assert gateway.tasks.index("director_event_design") < gateway.tasks.index("director_npc_create")
    with stores.world_setting as store:
        assert store.list_all() == []
    with stores.director_npc as store:
        assert store.list_all() == []
    with stores.director_event as store:
        assert store.list_all() == []




def test_event_selector_omits_and_rejects_previously_used_setting_fact_id():
    from app.domain.director_event.selector import build_event_creation_inputs
    from app.domain.director_event.design import DirectorEventDesigner
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

    selection = DirectorEventDesigner(gateway).design(input_data=build_event_creation_inputs(
        events=[previous],
        current_state=make_current_state(),
        core_memory=CoreMemory(items=[]),
        settings=[setting],
    ))

    [selected_setting] = json.loads(gateway.input_data[0]["settings_json"])
    [previous_event] = json.loads(gateway.input_data[0]["events_json"])
    assert previous_event["conception"] == "独立世界事件。"
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
        DirectorEventDesigner(rejected_gateway).design(input_data=build_event_creation_inputs(
            events=[previous],
            current_state=make_current_state(),
            core_memory=CoreMemory(items=[]),
            settings=[setting],
        ))

    # The prepared inputs also work through the real prompt renderer. accepts the same prepared input with a smaller output.
    from app.core.llm import MockLLM
    from app.domain.director_event.design import DirectorEventDesign, DirectorEventDesigner

    candidate_output = {
        key: value for key, value in available_output.items()
        if key in DirectorEventDesign.model_fields
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(candidate_output, ensure_ascii=False)])
    prepared_input = deepcopy(gateway.input_data[0])
    design = DirectorEventDesigner(mock.build_gateway()).design(input_data=prepared_input)

    assert design.model_dump(mode="json") == candidate_output
    assert prepared_input == gateway.input_data[0]
    assert set(design.model_dump()) == {
        "current_story_direction", "conception", "subject", "intent",
        "actor_roles", "additional_npcs", "selected_setting_fact_ids",
    }


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
    assert "director_event_design" not in gateway.tasks


def test_create_event_rejects_more_than_three_new_actors():
    from app.domain.director.skill_specs import CreateEventArguments

    with pytest.raises(ValidationError, match="at most 3 new actors"):
        CreateEventArguments(
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
    event = selection.to_event( turn_index=3, current_clock=WorldClock()
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


def test_design_actor_creation_keeps_existing_actors_and_shares_full_profiles(monkeypatch):
    from app.domain.creative_brief import CreativeBrief
    from app.domain.director_event.design import DirectorEventDesign
    from app.domain.director_event.design_actors import DirectorEventActorCreator
    from app.domain.director_npc.creator import DirectorNPCCreator
    from app.domain.world_setting import WorldSetting
    from app.tests.factories.director import make_director_npc

    existing = make_director_npc()
    required = [
        {"actor_key": "existing", "source": "existing", "npc_id": existing.draft_id,
         "guidance": "提供藏身处", "profile": existing.private_view().model_dump(mode="json")},
        {"actor_key": "new", "source": "new", "guidance": "接应人"},
    ]
    design = DirectorEventDesign(
        current_story_direction="调查河岸旧案", conception="接应持有账本的逃亡者",
        subject="接应人", intent="接走逃亡者", selected_setting_fact_ids=["fact_1", "fact_4"],
        actor_roles=[
            {"actor_key": "existing", "role_in_event": "提供藏身处"},
            {"actor_key": "new", "role_in_event": "在码头接应"},
        ],
        additional_npcs=[{"actor_key": "fugitive", "role_in_event": "持有账本的逃亡者"}],
    )
    setting = WorldSetting(kind="location", name="码头", summary="河岸货运码头",
                           public_facts=["设有渡口", "码头设有仓库"],
                           hidden_facts=["仓库地下另有暗室", "仓库留有旧账本"], created_turn=1)
    calls = []

    def create(self, **kwargs):
        calls.append(deepcopy(kwargs))
        return make_director_npc().model_copy(update={"name": f"新人物{len(calls)}"})

    monkeypatch.setattr(DirectorNPCCreator, "create", create)
    reserved = [{"name": "预备人物"}]
    drafts = DirectorEventActorCreator(object()).create(
        design=design, required_actors=required, core_memory=CoreMemory(items=[]),
        settings=[setting], existing_npcs=[], reserved_npcs=reserved,
        protagonist_name="主角", turn_index=3,
    )

    assert list(drafts) == ["new", "fugitive"]
    assert calls[0]["guidance"] == "接应人\n在本事件中的职责：在码头接应"
    assert calls[1]["guidance"] == "持有账本的逃亡者"
    initial = calls[0]["recent_creations"][0]
    assert initial["required_actors"] == required
    assert initial["conception"] == design.conception
    prior = calls[1]["recent_creations"][1]
    assert prior["actor_key"] == "new"
    assert prior["draft_id"] == drafts["new"].draft_id
    assert prior["hidden_secret"] == drafts["new"].hidden_secret
    assert prior["known_abilities"] == drafts["new"].private_view().model_dump(mode="json")["known_abilities"]
    assert calls[0]["reserved_npcs"][-1]["name"] == existing.name
    assert calls[1]["reserved_npcs"][-1]["name"] == drafts["new"].name
    assert all(c["creation_context"].setting_ids == [setting.setting_id] for c in calls)
    assert all(isinstance(c["creative_brief"], CreativeBrief) for c in calls)
    assert reserved == [{"name": "预备人物"}]
    assert all(c["settings"][0].public_facts == setting.public_facts for c in calls)
    assert all(c["settings"][0].hidden_facts == ["仓库留有旧账本"] for c in calls)
    assert setting.hidden_facts == ["仓库地下另有暗室", "仓库留有旧账本"]

    from app.core.llm import MockLLM
    from app.domain.director_event.composition import DirectorEventComposer, DirectorEventComposition

    output = {
        key: value for key, value in make_event_selection_output().items()
        if key in DirectorEventComposition.model_fields
    }
    output["current_situation"] = ["逃亡者持有账本，藏在码头。"]
    output["plan"] = "先与逃亡者确认身份，取到账本后安排渡船离开码头。"
    mock = MockLLM()
    mock.set_responses([json.dumps(output, ensure_ascii=False)])
    gateway = mock.build_gateway()
    composition_calls = []
    original_run = gateway.run

    def record(**kwargs):
        composition_calls.append(deepcopy(kwargs["input_data"]))
        return original_run(**kwargs)

    monkeypatch.setattr(gateway, "run", record)
    from app.domain.director_event.selector import _setting_fact_view

    input_data = {
        "core_memory_json": CoreMemory(items=[]).model_dump_json(),
        "current_state_json": make_current_state().model_dump_json(),
        "recent_summaries_json": "[]",
        "settings_json": json.dumps([_setting_fact_view(setting, used_fact_ids=set())]),
        "events_json": "[]",
        "director_guidance": "接应持有账本的逃亡者。",
    }
    result = DirectorEventComposer(gateway).compose(
        input_data=input_data, design=design, required_actors=required, new_npcs=drafts,
    )
    assert result == DirectorEventComposition.model_validate(output)
    supplied = composition_calls[0]
    [projected_setting] = json.loads(supplied["settings_json"])
    assert [f["content"] for f in projected_setting["public_facts"]] == setting.public_facts
    assert projected_setting["hidden_facts"] == [
        {"fact_id": "fact_4", "content": "仓库留有旧账本"},
    ]
    assert len(json.loads(input_data["settings_json"])[0]["hidden_facts"]) == 2
    assert json.loads(supplied["event_design_json"]) == design.model_dump(mode="json")
    actors = json.loads(supplied["actors_json"])
    assert [actor["actor_key"] for actor in actors] == ["existing", "new", "fugitive"]
    assert actors[0]["profile"] == required[0]["profile"]
    assert actors[1]["profile"] == drafts["new"].private_view().model_dump(mode="json")
    assert actors[2]["profile"] == drafts["fugitive"].private_view().model_dump(mode="json")
    assert "event_design_json" not in input_data
    with pytest.raises(ValueError, match="new_npcs"):
        DirectorEventComposer(gateway).compose(
            input_data=input_data, design=design, required_actors=required, new_npcs={},
        )
    assert len(composition_calls) == 1
