import pytest

import json
import threading
from pydantic import ValidationError
from app.domain.director import DirectorSkillRequest
from app.domain.director import DirectorSkillService
from app.domain.core_memory import CoreMemory
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.director.confirmation import DirectorConfirmationExecutor
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import make_current_state
from app.tests.factories.director import make_decision_batch
from app.tests.factories.director import make_event_selection_output
from app.tests.factories.director_schemas import SkillEventSelectionArguments


def test_created_setting_mention_in_same_confirmation_is_counted(tmp_path):
    from app.domain.world_setting import WorldSettingStore

    def run_with(scene_text, data_dir):
        stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(data_dir))
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
        DirectorConfirmationExecutor(
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
        ).run()
        with stores.world_setting as store:
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
        "actors": [{"source": "existing", "npc_id": "npc_known"}],
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
            "actors": [{"source": "existing", "npc_id": "npc_known"}],
        })

    with pytest.raises(ValidationError):
        CreateEventArguments.model_validate({
            "role": "arc",
            "setting_requirements": [
                {"kind": "lore", "guidance": f"抽象规则 {index}"}
                for index in range(5)
            ],
            "actors": [{"source": "existing", "npc_id": "npc_known"}],
        })


def test_create_event_requires_at_least_one_real_actor():
    from app.domain.director.skill_specs import CreateEventArguments

    base = {
        "role": "arc",
        "setting_requirements": [
            {"kind": "location", "setting_description": "依托山道驿站。"},
        ],
    }
    with pytest.raises(ValidationError, match="at least one real actor"):
        CreateEventArguments.model_validate(base)
    with pytest.raises(ValidationError, match="at least one real actor"):
        CreateEventArguments.model_validate({**base, "actors": []})

    existing = CreateEventArguments.model_validate({
        **base,
        "actors": [{"source": "existing", "npc_id": "npc_1"}],
    })
    assert existing.actors[0].source == "existing"

    mixed = CreateEventArguments.model_validate({
        **base,
        "actors": [
            {"source": "existing", "npc_id": "npc_1"},
            {"source": "new", "guidance": "新人物。"},
        ],
    })
    assert [actor.source for actor in mixed.actors] == ["existing", "new"]


def test_confirmation_agent_creates_settings_then_runs_wave_b_for_all_requests(
    tmp_path,
):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
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

    existing_actor = NPCProfile(name="寒川驿丞", role="驿站主管")
    with stores.npc_profile as store:
        store.upsert(existing_actor)

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
                        "actors": [
                            {
                                "source": "existing",
                                "npc_id": existing_actor.npc_id,
                                "guidance": "由既有驿站主管充当演员。",
                            },
                        ],
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
        event_selection_output=make_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "处置驿站往来"},
        ]),
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

    assert [execution.skill for execution in executions.executions] == [
        "create_npc", "create_event"
    ]
    assert len(setting_worker_ids) == 2
    assert executions.executions[0].output["name"] == "沈栖舟"
    assert executions.executions[1].output["event_id"].startswith("director_event_")
    with stores.world_setting as store:
        settings = store.list_all()
    assert {setting.name for setting in settings} == {"北陆商盟", "寒川驿"}
    with stores.director_npc as store:
        assert [npc.name for npc in store.list_all()] == ["沈栖舟"]
    with stores.director_event as store:
        assert len(store.list_all()) == 1


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


def test_skill_guidance_is_forwarded_into_create_npc_prompt(tmp_path):
    from app.domain.director.schemas import DirectorGuidance
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="醉花楼",
        summary="城中最大的歌舞酒楼，也是大额生意往来的地点。",
        public_facts=["二楼雅间常谈大宗买卖。"],
        hidden_facts=["掌柜暗中把持着几条商路。"],
        created_turn=1,
    )
    with stores.world_setting as store:
        store.save(setting)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "创作备用 NPC 草稿。",
            "requests": [
                {
                    "request_id": "create_1",
                    "skill": "create_npc",
                    "arguments": {
                        "setting_requirements": [{
                            "kind": "location",
                            "setting_description": "她长期在醉花楼二楼雅间谈生意。",
                            "matched_setting_id": setting.setting_id,
                        }],
                    },
                    "guidance": DirectorGuidance(
                        name="苏锦屏",
                        purpose="作为未来商业线索的交涉人。",
                        description="出身行商世家，隐瞒真实货主身份。",
                    ),
                }
            ],
        },
        npc_output={
            "creation_reason": "创作备用商业交涉人。",
            "identity": {
                "core_motivation": "暗中积累资产摆脱家族控制",
                "hidden_secret": "私藏有一批走私灵石",
                "role": "行商谈判代表",
                "faction": "行商会",
            },
            "behavior": {
                "conflict_style": "以利诱之，谋定后动",
                "social_style": "八面玲珑",
                "personality_trait": "外圆内方",
            },
            "shell": {
                "name": "苏锦屏",
                "appearance": "锦缎罗裙，神情精明。",
                "known_abilities": [{"name": "识货", "description": "能辨识珍品"}],
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

    assert decision.requests[0].skill == "create_npc"
    assert executions.executions[0].guidance == "苏锦屏: 作为未来商业线索的交涉人。"
    npc_input = gateway.input_data[-1]
    assert "苏锦屏" in npc_input["director_guidance"]
    assert "作为未来商业线索的交涉人" in npc_input["director_guidance"]
