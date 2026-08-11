import pytest

import json
import threading
from pydantic import ValidationError
from app.domain.director import DirectorSkillRequest
from app.domain.director import DirectorSkillService
from app.domain.core_memory import CoreMemory
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.director.confirmation import run_confirmation_director_agent
from app.runtime.paths import CampaignPaths
from app.runtime.stores import StoreBundle
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import make_current_state
from app.tests.factories.director import make_decision_batch
from app.tests.factories.director import make_event_selection_output
from app.tests.factories.director_schemas import SkillEventSelectionArguments


def test_created_setting_mention_in_same_confirmation_is_counted(tmp_path):
    from app.domain.world_setting import WorldSettingStore

    def run_with(scene_text, data_dir):
        stores = StoreBundle(CampaignPaths.from_data_dir(data_dir))
        gateway = ScriptedDirectorGateway(
            decision={
                "reasoning": "创建一名以本地航运为生的人物。",
                "requests": [{
                    "request_id": "npc_1",
                    "skill": "create_npc",
                    "arguments": {
                        "setting_requirements": [{
                            "kind": "faction",
                            "setting_description": "掌控北境商路的商会联盟，对边境贸易影响深远。",
                            "matched_setting_id": None,
                        }],
                    },
                    "guidance": "创建一名北陆商盟的成员。",
                }],
            },
            setting_output={
                "name": "北陆商盟",
                "summary": "掌控北境商路的商会联盟。",
                "public_facts": ["统一收取过路费。"],
                "hidden_facts": ["商会暗中资助边境私军。"],
            },
            npc_output={
                "creation_reason": "主角走访北境商路。",
                "identity": {
                    "core_motivation": "保住商队往来北境的路权。",
                    "hidden_secret": "北陆商盟暗中资助边境私军。",
                    "role": "北境商队执事",
                    "faction": None,
                },
                "behavior": {
                    "conflict_style": "先谈利益，谈不拢就断其商路。",
                    "social_style": "圆滑而强势。",
                    "personality_trait": "谈事先报价，把每笔往来都摆到台面上",
                },
                "shell": {
                    "known_abilities": [
                        {"name": "商事谈判", "description": "压价与斡旋贸易条件"},
                        {"name": "押运调度", "description": "安排商队路线与护卫"},
                    ],
                    "appearance": "常穿厚重皮袍。",
                    "name": "柴行简",
                },
            },
        )
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=make_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            turn_index=2,
            trace_id="confirmation-director",
            scene_text=scene_text,
        )
        with stores.world_setting() as store:
            return store.list_all()

    [counted] = run_with(
        "柴行简提到北陆商盟的规矩。",
        tmp_path / "matched",
    )
    assert counted.name == "北陆商盟"
    assert counted.created_turn == 2
    assert counted.hit_count == 1

    [unmatched] = run_with(
        "正文没有提到任何新设定。",
        tmp_path / "unmatched",
    )
    assert unmatched.name == "北陆商盟"
    assert unmatched.hit_count == 0


def test_setting_requirements_accepts_exactly_one_entry_with_optional_id():
    from app.domain.director.skill_specs import CreateEventArguments

    validated = CreateEventArguments.model_validate({
        "role": "arc",
        "setting_requirements": [
            {
                "kind": "location",
                "setting_description": "需要一个山道驿站。",
                "matched_setting_id": "setting_known",
            },
        ],
    })
    assert [item.kind for item in validated.setting_requirements] == ["location"]
    assert validated.setting_requirements[0].matched_setting_id == "setting_known"

    with pytest.raises(ValidationError, match="at most 1 item"):
        CreateEventArguments.model_validate({
            "role": "arc",
            "setting_requirements": [
                {"kind": "lore", "guidance": "规则一。"},
                {"kind": "item", "guidance": "规则二。"},
            ],
        })

    with pytest.raises(ValidationError):
        CreateEventArguments.model_validate({
            "role": "arc",
            "setting_requirements": [
                {"kind": "lore", "guidance": f"抽象规则 {index}"}
                for index in range(5)
            ],
        })


def test_confirmation_agent_creates_missing_setting_then_event_in_same_turn(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "事件需要一块尚未存在的生态土壤。",
            "requests": [{
                "request_id": "event_1",
                "skill": "create_event",
                "arguments": {
                    "role": "arc",
                    "setting_requirements": [
                        {
                            "kind": "faction",
                            "setting_description": "需要一个能长期控制货运的北境势力。",
                        },
                    ],
                },
                "guidance": "扩展北境货运生态。",
            }],
        },
        setting_output={
            "name": "北陆商盟",
            "summary": "控制北境货运节点并协调沿途商队的松散联盟。",
            "public_facts": ["联盟在主要驿站设有货栈。"],
            "hidden_facts": ["部分执事私下垄断紧缺货物。"],
        },
        event_selection_output=make_event_selection_output(),
    )

    _, executions = run_confirmation_director_agent(
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
    )

    assert executions.executions[0].output["event_id"].startswith("director_event_")
    assert gateway.tasks == [
        "director_agent_decide_confirmation",
        "director_setting_create",
        "director_event_select_arc",
    ]
    with stores.world_setting() as store:
        [setting] = store.list_all()
    assert setting.name == "北陆商盟"
    with stores.director_event() as store:
        [event] = store.list_all()
    assert event.source_setting_ids == [setting.setting_id]


def test_confirmation_agent_replaces_kind_mismatched_setting_before_execution(
    tmp_path,
):
    from app.domain.world_setting import WorldSetting

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="盐雾港",
        summary="依靠潮汐盐田与近海货运生存的港镇。",
        public_facts=["港民熟悉潮汐航道"],
        hidden_facts=["旧盐仓下有走私暗渠"],
        created_turn=1,
    )
    with stores.world_setting() as store:
        store.save(setting)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "把地点误声明成势力，应按声明类型补建。",
            "requests": [{
                "request_id": "event_1",
                "skill": "create_event",
                "arguments": {
                    "role": "arc",
                    "setting_requirements": [
                        {
                            "kind": "faction",
                            "setting_description": "需要一个能承载事件的货运势力。",
                            "matched_setting_id": setting.setting_id,
                        },
                    ],
                },
                "guidance": "扩展货运生态。",
            }],
        },
        setting_output={
            "name": "北陆商盟",
            "summary": "控制北境货运节点并协调沿途商队的松散联盟。",
            "public_facts": ["联盟在主要驿站设有货栈。"],
            "hidden_facts": ["部分执事私下垄断紧缺货物。"],
        },
        event_selection_output=make_event_selection_output(),
    )

    _, executions = run_confirmation_director_agent(
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
    )

    assert executions.executions[0].output["event_id"].startswith("director_event_")
    with stores.world_setting() as store:
        settings = store.list_all()
    assert {item.kind for item in settings} == {"location", "faction"}
    created = next(item for item in settings if item.kind == "faction")
    with stores.director_event() as store:
        [event] = store.list_all()
    assert event.source_setting_ids == [created.setting_id]


def test_confirmation_agent_creates_settings_then_runs_wave_b_for_all_requests(
    tmp_path,
):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    setting_barrier = threading.Barrier(2)
    setting_worker_ids: set[int] = set()

    def setting_output(data):
        setting_worker_ids.add(threading.get_ident())
        setting_barrier.wait(timeout=2)
        if data["kind"] == "faction":
            return {
                "name": "北陆商盟",
                "summary": "控制北境货运节点并协调沿途商队的松散联盟。",
                "public_facts": ["联盟在主要驿站设有货栈。"],
                "hidden_facts": ["部分执事私下垄断紧缺货物。"],
            }
        return {
            "name": "寒川驿",
            "summary": "连接北境山道和河运的交通节点。",
            "public_facts": ["商旅会在此更换车马。"],
            "hidden_facts": ["地下仓道通往废弃河港。"],
        }

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "两个技能各自缺少一个 Setting，补齐后都应执行。",
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
                        "actors": [],
                    },
                        "guidance": "建立独立环境事件。",
                },
            ],
        },
        setting_output=setting_output,
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
        event_selection_output=make_event_selection_output(),
    )

    _, executions = run_confirmation_director_agent(
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
    )

    assert [execution.skill for execution in executions.executions] == [
        "create_npc", "create_event"
    ]
    assert len(setting_worker_ids) == 2
    assert executions.executions[0].output["name"] == "沈栖舟"
    assert executions.executions[1].output["event_id"].startswith("director_event_")
    with stores.world_setting() as store:
        settings = store.list_all()
    assert {setting.name for setting in settings} == {"北陆商盟", "寒川驿"}
    with stores.director_npc() as store:
        assert [npc.name for npc in store.list_all()] == ["沈栖舟"]
    with stores.director_event() as store:
        assert len(store.list_all()) == 1


def test_confirmation_agent_replaces_unknown_setting_reference(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "引用了尚不存在的设定，应改为补建该设定。",
            "requests": [{
                "request_id": "npc_1",
                "skill": "create_npc",
                "arguments": {
                    "setting_requirements": [{
                        "kind": "faction",
                        "setting_description": "需要一个尚未存在的北境势力。",
                        "matched_setting_id": "setting_future",
                    }],
                },
                "guidance": "人物依托一块尚未存在的土壤。",
            }],
        },
        setting_output={
            "name": "北陆商盟",
            "summary": "控制北境货运节点并协调沿途商队的松散联盟。",
            "public_facts": ["联盟在主要驿站设有货栈。"],
            "hidden_facts": ["部分执事私下垄断紧缺货物。"],
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

    _, executions = run_confirmation_director_agent(
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
    )

    assert executions.executions[0].output["name"] == "沈栖舟"
    with stores.world_setting() as store:
        [setting] = store.list_all()
    assert setting.kind == "faction"
    with stores.director_npc() as store:
        [npc] = store.list_all()
    assert npc.source_setting_ids == [setting.setting_id]


def test_confirmation_agent_can_enrich_new_npc_via_skill(tmp_path):
    import json

    from app.domain.world_setting import WorldSetting

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="醉花楼",
        summary="城中最大的歌舞酒楼，也是大额生意往来的地点。",
        public_facts=["二楼雅间常谈大宗买卖。"],
        hidden_facts=["掌柜暗中把持着几条商路。"],
        created_turn=1,
    )
    with stores.world_setting() as store:
        store.save(setting)
    new_profile = NPCProfile(
        name="二楼女子",
        role="生意谈判方",
        appearance="声音温婉中带媚。",
        public_summary="醉花楼二楼雅间中参与大额生意谈判的女子。",
    )
    new_id = new_profile.npc_id
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "新出现的谈判方缺少幕后设定，需要补全。",
            "requests": [
                {
                    "request_id": "enrich_1",
                    "skill": "enrich_npc",
                    "arguments": {
                        "npc_id": new_id,
                        "setting_requirements": [{
                            "kind": "location",
                            "setting_description": "她长期在醉花楼二楼雅间谈生意。",
                            "matched_setting_id": setting.setting_id,
                        }],
                    },
                    "guidance": "补全谈判方的完整人设。",
                }
            ],
        },
        backfill_output={
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
    )

    decision, executions = run_confirmation_director_agent(
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
        new_npc_profiles=[new_profile],
    )

    assert decision.requests[0].skill == "enrich_npc"
    assert executions.executions[0].output["name"] == "苏锦屏"
    assert gateway.tasks == ["director_agent_decide_confirmation", "npc_profile_backfill"]
    backfill_input = gateway.input_data[1]
    assert setting.name in backfill_input["settings_json"]
    assert "醉花楼" in backfill_input["settings_json"]
    creative_brief = json.loads(
        backfill_input["creative_brief_json"]
    )
    assert set(creative_brief) == {
        "world_connection",
        "life_phase",
        "desire_pattern",
        "baseline_temperament",
    }
    with stores.npc_profile() as npc_store:
        saved = npc_store.get_by_id(new_id)
    assert saved.name == "苏锦屏"
    assert "二楼女子" not in saved.aliases
    assert saved.transient_aliases["二楼女子"] == 6
    assert saved.core_motivation == "建立一张足以保护自身生意的合作网络。"
    assert saved.hidden_secret == "她正在隐瞒真正货主的身份。"
    assert saved.source_setting_ids == [setting.setting_id]


def test_confirmation_agent_rejects_enrich_npc_for_existing_profile(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="醉花楼",
        summary="城中最大的歌舞酒楼，也是大额生意往来的地点。",
        public_facts=["二楼雅间常谈大宗买卖。"],
        hidden_facts=["掌柜暗中把持着几条商路。"],
        created_turn=1,
    )
    with stores.world_setting() as store:
        store.save(setting)
    with stores.npc_profile() as npc_store:
        existing_id = npc_store.upsert(NPCProfile(name="陆远舟", role="镖师"))
        other_new_id = npc_store.upsert(NPCProfile(name="二楼女子", role="生意谈判方"))
        other_new = npc_store.get_by_id(other_new_id)
    assert other_new is not None
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "错误地补全既有 NPC。",
            "requests": [
                {
                    "request_id": "enrich_1",
                    "skill": "enrich_npc",
                    "arguments": {
                        "npc_id": existing_id,
                        "setting_requirements": [{
                            "kind": "location",
                            "setting_description": "错误地补全既有 NPC 的土壤。",
                            "matched_setting_id": setting.setting_id,
                        }],
                    },
                    "guidance": "错误地补全既有 NPC。",
                }
            ],
        },
        backfill_output={
            "name": "陆远舟",
            "identity": {
                "core_motivation": "护送商队平安。",
                "hidden_secret": "替人捎带一封密信。",
                "role": "镖师",
                "faction": None,
            },
            "behavior": {
                "conflict_style": "先护人后对敌。",
                "social_style": "寡言但可靠。",
                "personality_trait": "站在门口先扫一遍进出的人，再决定让不让路",
            },
            "shell": {
                "known_abilities": [{"name": "刀法", "description": "贴身护卫时的近战刀法"}],
                "appearance": "一身劲装。",
            },
        },
    )

    with pytest.raises(ValueError, match="enrich_npc requires a newly created npc_id"):
        run_confirmation_director_agent(
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
            new_npc_profiles=[other_new],
        )


def test_skill_guidance_flows_through_to_execution_handler():
    service = DirectorSkillService()
    seen_guidance: list[str | None] = []

    def handle(arguments: SkillEventSelectionArguments, guidance: str | None = None):
        seen_guidance.append(guidance)
        return {"presented_event_id": arguments.event_id}

    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=SkillEventSelectionArguments,
        handler=handle,
    )
    batch = make_decision_batch(
        reasoning="两个事件都与本轮行动直接相关。",
        requests=[
            DirectorSkillRequest(
                request_id="request_1",
                skill="present_event",
                arguments={"event_id": "event_a"},
                guidance="以压迫感为导向呈现该事件。",
            ),
            DirectorSkillRequest(
                request_id="request_2",
                skill="present_event",
                arguments={"event_id": "event_b"},
                guidance="以疏离感呈现该事件。",
            ),
        ],
    )

    result = service.execute_batch(batch, phase="generation")

    assert seen_guidance == [
        "以压迫感为导向呈现该事件。",
        "以疏离感呈现该事件。",
    ]
    assert [execution.guidance for execution in result.executions] == [
        "以压迫感为导向呈现该事件。",
        "以疏离感呈现该事件。",
    ]
    assert result.executions[0].guidance == "以压迫感为导向呈现该事件。"


def test_skill_guidance_is_forwarded_into_execution_prompt(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="醉花楼",
        summary="城中最大的歌舞酒楼，也是大额生意往来的地点。",
        public_facts=["二楼雅间常谈大宗买卖。"],
        hidden_facts=["掌柜暗中把持着几条商路。"],
        created_turn=1,
    )
    with stores.world_setting() as store:
        store.save(setting)
    with stores.npc_profile() as npc_store:
        new_id = npc_store.upsert(
            NPCProfile(
                name="二楼女子",
                role="生意谈判方",
                appearance="声音温婉中带媚。",
                public_summary="醉花楼二楼雅间中参与大额生意谈判的女子。",
            )
        )
        new_profile = npc_store.get_by_id(new_id)
    assert new_profile is not None
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "新出现的谈判方需要按指定方向补全。",
            "requests": [
                {
                    "request_id": "enrich_1",
                    "skill": "enrich_npc",
                    "arguments": {
                        "npc_id": new_id,
                        "setting_requirements": [{
                            "kind": "location",
                            "setting_description": "她长期在醉花楼二楼雅间谈生意。",
                            "matched_setting_id": setting.setting_id,
                        }],
                    },
                    "guidance": "出身行商世家，隐瞒真实货主身份。",
                }
            ],
        },
        backfill_output={
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
    )

    decision, executions = run_confirmation_director_agent(
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
        new_npc_profiles=[new_profile],
    )

    assert decision.requests[0].guidance == "出身行商世家，隐瞒真实货主身份。"
    assert executions.executions[0].guidance == "出身行商世家，隐瞒真实货主身份。"
    backfill_input = gateway.input_data[-1]
    assert backfill_input["director_guidance"] == "出身行商世家，隐瞒真实货主身份。"
