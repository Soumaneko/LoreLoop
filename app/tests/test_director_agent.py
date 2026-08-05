import json
import threading
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import pytest

from app.core.llm import LLMOutputValidationError, MockLLM, PromptLoader
from app.domain.director import (
    DirectorAgent,
    DirectorDecisionBatch,
    DirectorObservation,
    DirectorSkillRequest,
    DirectorSkillService,
)
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState, WorldClock
from app.domain.director_npc import DirectorNPCDraft
from app.domain.npc_profile.schemas import (
    NPCExtractionResult,
    NPCMention,
    NPCProfile,
)
from app.domain.scene_facts.schemas import SceneFactSet
from app.graph import confirmation_nodes
from app.graph.director_agent import (
    run_confirmation_director_agent,
    run_generation_director_agent,
)
from app.runtime.paths import CampaignPaths
from app.runtime.stores import StoreBundle


def _decision_batch(**kwargs) -> DirectorDecisionBatch:
    return DirectorDecisionBatch(**kwargs)


class _PresentEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)


class _CreateEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)


class _CreateNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


def _observation(phase: str = "generation") -> DirectorObservation:
    return DirectorObservation(
        phase=phase,
        turn_index=3,
        player_input="前往码头调查。",
        core_memory={"world_name": "测试世界"},
        current_state={"scene": {"location": "旧码头"}},
    )


def test_skill_service_exposes_only_skills_available_in_phase():
    service = DirectorSkillService()
    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation", "confirmation"},
        mutates=False,
        arguments_model=_PresentEventArguments,
        handler=lambda arguments, guidance=None: {"event_id": arguments.event_id},
    )
    service.register(
        name="create_event",
        description="创建新的导演事件。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=_CreateEventArguments,
        handler=lambda arguments, guidance=None: {"title": arguments.title},
    )

    assert [
        spec.name for spec in service.specs_for_phase("generation")
    ] == ["present_event"]
    assert [
        spec.name for spec in service.specs_for_phase("confirmation")
    ] == ["present_event", "create_event"]


def test_skill_service_rejects_mutating_generation_skill():
    service = DirectorSkillService()

    with pytest.raises(
        ValueError,
        match="mutating director skills cannot run during generation",
    ):
        service.register(
            name="create_event",
            description="创建新的导演事件。",
            phases={"generation"},
            mutates=True,
            arguments_model=_CreateEventArguments,
            handler=lambda arguments, guidance=None: None,
        )


def test_skill_service_executes_wave_b_concurrently_and_preserves_order():
    service = DirectorSkillService()
    barrier = threading.Barrier(2)
    worker_ids: set[int] = set()

    def handle(arguments, guidance=None):
        worker_ids.add(threading.get_ident())
        barrier.wait(timeout=2)
        return {"name": arguments.name}

    service.register(
        name="create_npc",
        description="创建人物。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=_CreateNPCArguments,
        handler=handle,
    )
    batch = _decision_batch(
        reasoning="两个无冲突人物可以同时创建。",
        requests=[
            DirectorSkillRequest(
                request_id=f"npc_{index}",
                skill="create_npc",
                arguments={"name": name},
                guidance="创建独立人物。",
            )
            for index, name in enumerate(["甲", "乙"], start=1)
        ],
    )

    result = service.execute_batch(
        batch,
        phase="confirmation",
        max_workers=2,
    )

    assert len(worker_ids) == 2
    assert [execution.output["name"] for execution in result.executions] == [
        "甲", "乙"
    ]


def test_generation_skill_requires_request_guidance():
    service = DirectorSkillService()
    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=_PresentEventArguments,
        handler=lambda arguments, guidance=None: None,
    )
    batch = _decision_batch(
        reasoning="需要呈现事件。",
        requests=[
            DirectorSkillRequest(
                request_id="request_1",
                skill="present_event",
                arguments={"event_id": "event_1"},
                guidance="   ",
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match="director skills require guidance",
    ):
        service.validate_batch(batch, phase="generation")


def test_introduce_npc_requires_valid_view_mode():
    from app.graph.director_skill_specs import IntroduceNPCArguments

    with pytest.raises(ValidationError):
        IntroduceNPCArguments(npc_id="npc_1")
    with pytest.raises(ValidationError):
        IntroduceNPCArguments(npc_id="npc_1", view_mode="scry_all")
    validated = IntroduceNPCArguments(
        npc_id="npc_1",
        view_mode="face_to_face",
    )
    assert validated.view_mode == "face_to_face"


def test_skill_service_validates_arguments_and_executes_in_request_order():
    service = DirectorSkillService()
    called: list[str] = []

    def handle(arguments: _PresentEventArguments, guidance: str | None = None):
        called.append(arguments.event_id)
        return {"presented_event_id": arguments.event_id}

    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=_PresentEventArguments,
        handler=handle,
    )
    batch = _decision_batch(
        reasoning="两个事件都与本轮行动直接相关。",
        requests=[
            DirectorSkillRequest(
                request_id="request_1",
                skill="present_event",
                arguments={"event_id": "event_a"},
                guidance="让事件通过码头异动进入场景。",
            ),
            DirectorSkillRequest(
                request_id="request_2",
                skill="present_event",
                arguments={"event_id": "event_b"},
                guidance="让事件通过远处传闻进入场景。",
            ),
        ],
    )

    result = service.execute_batch(batch, phase="generation")

    assert called == ["event_a", "event_b"]
    assert [
        execution.output["presented_event_id"]
        for execution in result.executions
    ] == ["event_a", "event_b"]

    invalid_batch = _decision_batch(
        reasoning="参数错误。",
        requests=[
                DirectorSkillRequest(
                    request_id="request_3",
                    skill="present_event",
                    arguments={},
                    guidance="呈现事件影响。",
                )
        ],
    )
    with pytest.raises(ValidationError):
        service.validate_batch(invalid_batch, phase="generation")


def test_skill_service_rejects_duplicate_requests_and_invalid_resources():
    service = DirectorSkillService()

    def validate_event(arguments):
        if arguments.event_id != "event_1":
            raise ValueError("unknown event")

    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=_PresentEventArguments,
        handler=lambda arguments, guidance=None: None,
        arguments_validator=validate_event,
    )
    duplicate = _decision_batch(
        reasoning="错误地重复请求。",
        requests=[
            DirectorSkillRequest(
                request_id="one",
                skill="present_event",
                arguments={"event_id": "event_1"},
                guidance="呈现事件影响。",
            ),
            DirectorSkillRequest(
                request_id="two",
                skill="present_event",
                arguments={"event_id": "event_1"},
                guidance="呈现事件影响。",
            ),
        ],
    )

    with pytest.raises(ValueError, match="duplicate director skill request"):
        service.validate_batch(duplicate, phase="generation")

    unknown = _decision_batch(
        reasoning="引用了不存在的资源。",
        requests=[
            DirectorSkillRequest(
                request_id="three",
                skill="present_event",
                arguments={"event_id": "event_missing"},
                guidance="呈现事件影响。",
            ),
        ],
    )
    with pytest.raises(ValueError, match="unknown event"):
        service.validate_batch(unknown, phase="generation")


def test_skill_service_treats_guidance_as_part_of_request_uniqueness():
    service = DirectorSkillService()
    service.register(
        name="create_event",
        description="创建新的导演事件。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=_CreateEventArguments,
        handler=lambda arguments, guidance=None: {"title": arguments.title},
    )
    distinct = _decision_batch(
        reasoning="两次同参数但方向不同的创建。",
        requests=[
            DirectorSkillRequest(
                request_id="one",
                skill="create_event",
                arguments={"title": "码头暗涌"},
                guidance="码头势力正在暗中重整旗鼓。",
            ),
            DirectorSkillRequest(
                request_id="two",
                skill="create_event",
                arguments={"title": "码头暗涌"},
                guidance="城中旧案出现新的线索。",
            ),
        ],
    )

    service.validate_batch(distinct, phase="confirmation")
    executed = service.execute_batch(distinct, phase="confirmation")

    assert [execution.guidance for execution in executed.executions] == [
        "码头势力正在暗中重整旗鼓。",
        "城中旧案出现新的线索。",
    ]

    duplicated = _decision_batch(
        reasoning="同技能同参数且 guidance 相同，视为重复。",
        requests=[
            DirectorSkillRequest(
                request_id="one",
                skill="create_event",
                arguments={"title": "码头暗涌"},
                guidance="码头势力正在暗中重整旗鼓。",
            ),
            DirectorSkillRequest(
                request_id="two",
                skill="create_event",
                arguments={"title": "码头暗涌"},
                guidance="码头势力正在暗中重整旗鼓。",
            ),
        ],
    )
    with pytest.raises(ValueError, match="duplicate director skill request"):
        service.validate_batch(duplicated, phase="confirmation")


def test_structured_guidance_forwards_name_purpose_and_discards_description():
    from app.domain.director import DirectorGuidance

    service = DirectorSkillService()
    service.register(
        name="create_npc",
        description="创建新的导演 NPC。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=_CreateNPCArguments,
        handler=lambda arguments, guidance=None: {"name": arguments.name},
    )
    batch = _decision_batch(
        reasoning="用结构化指导创建一个 NPC。",
        requests=[
            DirectorSkillRequest(
                request_id="npc_1",
                skill="create_npc",
                arguments={"name": "林澈"},
                guidance=DirectorGuidance(
                    name="林澈",
                    purpose="社区常驻的可靠医生。",
                    description="这是一行以内的背景蜜罐。",
                ),
            ),
        ],
    )

    executed = service.execute_batch(batch, phase="confirmation")

    assert executed.executions[0].guidance == "林澈: 社区常驻的可靠医生。"
    assert "蜜罐" not in executed.executions[0].guidance


def test_structured_guidance_dedup_ignores_description_honeypot():
    from app.domain.director import DirectorGuidance

    service = DirectorSkillService()
    service.register(
        name="create_npc",
        description="创建新的导演 NPC。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=_CreateNPCArguments,
        handler=lambda arguments, guidance=None: {"name": arguments.name},
    )
    duplicate = _decision_batch(
        reasoning="两个请求 name/purpose 相同、仅 description 不同，应视为重复。",
        requests=[
            DirectorSkillRequest(
                request_id="npc_1",
                skill="create_npc",
                arguments={"name": "林澈"},
                guidance=DirectorGuidance(
                    name="林澈",
                    purpose="社区常驻的可靠医生。",
                    description="第一版背景蜜罐。",
                ),
            ),
            DirectorSkillRequest(
                request_id="npc_2",
                skill="create_npc",
                arguments={"name": "林澈"},
                guidance=DirectorGuidance(
                    name="林澈",
                    purpose="社区常驻的可靠医生。",
                    description="第二版背景蜜罐，不影响执行。",
                ),
            ),
        ],
    )

    with pytest.raises(ValueError, match="duplicate director skill request"):
        service.validate_batch(duplicate, phase="confirmation")


def test_event_guidance_accepts_free_text():
    service = DirectorSkillService()
    service.register(
        name="create_event",
        description="创建新的导演事件。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=_CreateEventArguments,
        handler=lambda arguments, guidance=None: {"title": arguments.title},
    )
    batch = _decision_batch(
        reasoning="用自由文本方向创建事件。",
        requests=[
            DirectorSkillRequest(
                request_id="event_1",
                skill="create_event",
                arguments={"title": "码头暗涌"},
                guidance="围绕货运垄断在码头酝酿一场幕后冲突。",
            ),
        ],
    )

    executed = service.execute_batch(batch, phase="confirmation")

    assert executed.executions[0].guidance == (
        "围绕货运垄断在码头酝酿一场幕后冲突。"
    )


def test_director_agent_returns_empty_batch_without_available_skills():
    mock = MockLLM()
    mock.set_responses([
        """
        {
          "reasoning": "当前阶段没有可用的导演技能。",
          "requests": []
        }
        """
    ])
    agent = DirectorAgent(mock.build_gateway(), DirectorSkillService())

    result = agent.decide_generation(_observation())

    assert result.requests == []


def test_director_agent_uses_registered_skill_and_validates_selection():
    service = DirectorSkillService()
    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=_PresentEventArguments,
        handler=lambda arguments, guidance=None: None,
    )
    mock = MockLLM()
    mock.set_responses([
        """
        {
          "reasoning": "码头行动适合呈现已有事件。",
          "requests": [
            {
              "request_id": "request_1",
              "skill": "present_event",
              "arguments": {"event_id": "event_1"},
              "guidance": "通过码头异动侧面呈现事件。"
            }
          ]
        }
        """
    ])

    result = DirectorAgent(mock.build_gateway(), service).decide_generation(
        _observation(),
        trace_id="director-agent-test",
    )

    assert result.requests[0].skill == "present_event"
    assert result.requests[0].arguments == {"event_id": "event_1"}


def test_director_agent_rejects_unregistered_skill_selected_by_llm():
    service = DirectorSkillService()
    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=_PresentEventArguments,
        handler=lambda arguments, guidance=None: None,
    )
    mock = MockLLM()
    invalid_response = """
        {
          "reasoning": "错误地选择了未开放的技能。",
          "requests": [
            {
              "request_id": "request_1",
              "skill": "create_event",
              "arguments": {"title": "错误事件"},
              "guidance": "错误地创建事件。"
            }
          ]
        }
        """
    mock.set_responses([invalid_response, invalid_response, invalid_response])

    with pytest.raises(
        LLMOutputValidationError,
        match="unknown director skill: create_event",
    ):
        DirectorAgent(mock.build_gateway(), service).decide_generation(_observation())


def test_director_agent_prompt_is_chinese():
    generation_prompt, _ = PromptLoader().load("director_agent_decide_generation")
    confirmation_prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    for prompt in (generation_prompt, confirmation_prompt):
        assert "导演协调 Agent" in prompt
        assert "只输出符合 Schema 的 JSON" in prompt


def test_director_decide_prompt_describes_guidance():
    generation_prompt, _ = PromptLoader().load("director_agent_decide_generation")
    confirmation_prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    for prompt in (generation_prompt, confirmation_prompt):
        assert "`guidance`" in prompt
        assert "方向性指导意见" in prompt
        assert "不得在 `guidance` 中撰写具体正文" in prompt


def test_director_decide_prompts_are_phase_separated():
    generation_prompt, _ = PromptLoader().load("director_agent_decide_generation")
    confirmation_prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    assert "生成阶段每个技能请求都必须填写非空 `guidance`" in generation_prompt
    assert "`view_mode`" in generation_prompt
    assert "create_event" not in generation_prompt

    assert "世界设定的绝对依赖性" in confirmation_prompt
    assert "`create_event`" in confirmation_prompt
    assert "`setting_requirements`" in confirmation_prompt
    assert "`view_mode`" not in confirmation_prompt


class _PhaseGateway:
    def __init__(
        self,
        *,
        decision: dict,
        npc_output: dict | None = None,
        backfill_output: dict | None = None,
        event_selection_output: dict | None = None,
        event_review_output: dict | None = None,
        setting_output: dict | None = None,
    ):
        self.decision = decision
        self.npc_output = npc_output
        self.backfill_output = backfill_output
        self.event_selection_output = event_selection_output
        self.event_review_output = event_review_output
        self.setting_output = setting_output
        self.tasks: list[str] = []
        self.input_data: list[dict] = []

    def run(self, **kwargs):
        task = kwargs["task"]
        self.tasks.append(task)
        self.input_data.append(kwargs.get("input_data") or {})
        if task in {
            "director_agent_decide_generation",
            "director_agent_decide_confirmation",
        }:
            payload = dict(self.decision)
            payload.setdefault("requests", [])
        elif task.startswith("director_event_select_") and (
            self.event_selection_output is not None
        ):
            payload = self.event_selection_output
        elif task.startswith("director_event_review_") and (
            self.event_review_output is not None
        ):
            payload = self.event_review_output
        elif task == "director_npc_create" and self.npc_output is not None:
            payload = self.npc_output
        elif task == "npc_profile_backfill" and self.backfill_output is not None:
            payload = self.backfill_output
        elif task == "director_setting_create" and self.setting_output is not None:
            payload = (
                self.setting_output(kwargs.get("input_data") or {})
                if callable(self.setting_output)
                else self.setting_output
            )
        else:
            raise AssertionError(f"unexpected Director integration task: {task}")
        result = kwargs["output_schema"].model_validate(payload)
        validator = kwargs.get("output_validator")
        if validator is not None:
            validator(result)
        return result


def _current_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_1",
            location="河岸社区",
            participants=["主角"],
        ),
        player=PlayerState(name="主角"),
    )


def _director_npc() -> DirectorNPCDraft:
    return DirectorNPCDraft(
        name="林澈",
        role="社区医生",
        appearance="常穿深色风衣。",
        personality_traits=["谨慎", "耐心"],
        known_abilities=["急救"],
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        conflict_style="先协商后行动。",
        social_style="克制而友善。",
        encounter_potentials=["诊所求助", "街区冲突", "旧案线索"],
        created_turn=2,
    )


def test_generation_director_observes_setting_without_facts(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    with stores.world_setting() as store:
        store.save(setting)
        store.save(unrelated)
    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []},
    )

    run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="观察港口。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
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
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    prepared = _director_npc().model_copy(
        update={"draft_id": "director_npc_prepared"}
    )
    introduced = _director_npc().model_copy(
        update={
            "draft_id": "director_npc_introduced",
            "name": "顾晚棠",
            "status": "introduced",
            "introduced_turn": 2,
        }
    )
    with stores.director_npc() as store:
        store.save(prepared)
        store.save(introduced)
    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []},
    )

    run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="观察港口。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
    )

    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    observed_drafts = [
        item
        for item in observation["resources"]["npcs"]
        if item.get("source") == "director_npc"
    ]
    assert [item["npc_id"] for item in observed_drafts] == [
        "director_npc_prepared"
    ]
    assert "director_npc_introduced" not in {
        item["npc_id"] for item in observation["resources"]["npcs"]
    }


def test_generation_agent_stages_existing_npc_without_mutating_it(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    draft = _director_npc()
    with stores.director_npc() as store:
        store.save(draft)
    gateway = _PhaseGateway(
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
        current_state=_current_state(),
        user_input="去诊所询问昨夜发生的事。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
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
    with stores.director_npc() as store:
        assert store.get_by_id(draft.draft_id).status == "prepared"


def test_generation_agent_remote_view_filters_nonexistent_fields_for_director_npc(
    tmp_path,
):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    draft = _director_npc()
    with stores.director_npc() as store:
        store.save(draft)
    gateway = _PhaseGateway(
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
        current_state=_current_state(),
        user_input="去诊所询问昨夜发生的事。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
    )

    assert stage.materials[0].payload["source"] == "director_npc"
    assert "aliases" not in stage.materials[0].payload
    assert "public_summary" not in stage.materials[0].payload
    assert "attitude_to_player" not in stage.materials[0].payload
    assert stage.materials[0].payload["role"] == "社区医生"
    assert stage.materials[0].payload["social_style"] == "克制而友善。"


def test_generation_agent_intimate_view_reveals_director_npc_secrets(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    draft = _director_npc()
    with stores.director_npc() as store:
        store.save(draft)
    gateway = _PhaseGateway(
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
        current_state=_current_state(),
        user_input="对林澈施展搜魂术。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
    )

    assert stage.materials[0].payload["source"] == "director_npc"
    assert stage.materials[0].payload["core_motivation"] == "保护社区居民。"
    assert stage.materials[0].payload["hidden_secret"] == "曾目击一宗未结案件。"
    assert stage.materials[0].payload["appearance"] == "常穿深色风衣。"


def test_generation_agent_remote_view_keeps_profile_identity_and_drops_nulls(
    tmp_path,
):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile() as store:
        profile_id = store.upsert(
            NPCProfile(
                name="林澈",
                role="社区医生",
                aliases=["林医生"],
                social_style="克制而友善。",
            )
        )
    gateway = _PhaseGateway(
        decision={
            "reasoning": "按档案字段呈现人物。",
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

    stage, _ = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="去诊所询问昨夜发生的事。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
    )

    assert stage.materials[0].payload["source"] == "npc_profile"
    assert stage.materials[0].payload["name"] == "林澈"
    assert stage.materials[0].payload["role"] == "社区医生"
    assert stage.materials[0].payload["aliases"] == ["林医生"]
    assert stage.materials[0].payload["social_style"] == "克制而友善。"
    assert "public_summary" not in stage.materials[0].payload
    assert "attitude_to_player" not in stage.materials[0].payload
    assert "hidden_secret" not in stage.materials[0].payload


def test_generation_agent_receives_projected_turn_resolution(tmp_path):
    import json

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = _PhaseGateway(
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

    _, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="向码头工人打听沉船的事。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        turn_resolution_json=turn_resolution_json,
    )

    assert decision.requests == []
    decide_input = gateway.input_data[0]["director_observation_json"]
    observation = json.loads(decide_input)
    assert observation["turn_resolution"]["action_resolutions"][0]["player_action"] == (
        "向码头工人打听沉船的事。"
    )
    assert "action_resolutions" in observation["turn_resolution"]


def test_confirmation_agent_rejects_npc_creation_without_setting(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = _PhaseGateway(
        decision={
            "reasoning": "错误地在没有任何 Setting 依托的情况下创建人物。",
            "requests": [
                {
                    "request_id": "create_1",
                    "skill": "create_npc",
                    "arguments": {},
                    "guidance": "准备一个长期社区人物。",
                }
            ],
        },
        npc_output={
            "creation_reason": "社区日常需要一个可复用医生。",
            "core_motivation": "保护社区居民。",
            "hidden_secret": "曾目击一宗未结案件。",
            "conflict_style": "先协商后行动。",
            "social_style": "克制而友善。",
            "role": "社区医生",
            "faction": None,
            "personality_traits": ["谨慎", "耐心"],
            "known_abilities": ["急救"],
            "appearance": "常穿深色风衣。",
            "name": "林澈",
            "encounter_potentials": ["诊所求助", "街区冲突", "旧案线索"],
        },
    )

    with pytest.raises(ValidationError, match="setting_requirements"):
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            npc_profiles=[],
            turn_index=3,
            trace_id="confirmation-director",
        )

    with stores.director_npc() as store:
        assert store.list_prepared() == []
    with stores.world_setting() as store:
        assert store.list_all() == []


def test_confirmation_agent_passes_existing_setting_to_npc_creator(tmp_path):
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
    unrelated = setting.model_copy(update={
        "setting_id": "setting_unrelated",
        "name": "山邮驿",
        "summary": "负责内陆山路信件转运的驿站。",
    })
    with stores.world_setting() as store:
        store.save(setting)
        store.save(unrelated)
    gateway = _PhaseGateway(
        decision={
            "reasoning": "港镇需要一名长期人物。",
            "requests": [{
                "request_id": "npc_1",
                "skill": "create_npc",
                "arguments": {
                    "setting_requirements": [{
                        "kind": "location",
                        "setting_description": "人物以本地航运为生。",
                        "matched_setting_id": setting.setting_id,
                    }],
                },
                "guidance": "创建一名以本地航运为生的人物。",
            }],
        },
        npc_output={
            "creation_reason": "港镇日常需要熟悉潮路的船工。",
            "core_motivation": "保住家族赖以生存的小船。",
            "hidden_secret": "偶然发现旧盐仓暗渠仍有人使用。",
            "conflict_style": "先避让，退无可退时正面交涉。",
            "social_style": "寡言但会直接指出航路风险。",
            "role": "潮路船工",
            "faction": None,
            "personality_traits": ["谨慎", "务实"],
            "known_abilities": ["辨潮", "操舟"],
            "appearance": "皮肤被海风晒得黝黑。",
            "name": "周汐",
            "encounter_potentials": ["雇船", "避风", "港口纠纷"],
        },
    )

    run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    npc_input = gateway.input_data[gateway.tasks.index("director_npc_create")]
    assert setting.name in npc_input["settings_json"]
    assert setting.hidden_facts[0] in npc_input["settings_json"]
    assert unrelated.name not in npc_input["settings_json"]
    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    observed_settings = observation["resources"]["settings"]
    assert {
        item["setting_id"] for item in observed_settings
    } == {setting.setting_id, unrelated.setting_id}
    assert all("public_facts" not in item for item in observed_settings)
    assert all("hidden_facts" not in item for item in observed_settings)
    with stores.director_npc() as store:
        [saved] = store.list_prepared()
    assert saved.source_setting_ids == [setting.setting_id]


def test_confirmation_agent_observes_only_prepared_director_npcs(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    prepared = _director_npc().model_copy(
        update={"draft_id": "director_npc_conf_prepared"}
    )
    introduced = _director_npc().model_copy(
        update={
            "draft_id": "director_npc_conf_introduced",
            "name": "顾晚棠",
            "status": "introduced",
            "introduced_turn": 2,
        }
    )
    with stores.director_npc() as store:
        store.save(prepared)
        store.save(introduced)
    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []},
    )

    run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    observed_drafts = [
        item
        for item in observation["resources"]["npcs"]
        if item.get("source") == "director_npc"
    ]
    assert [item["npc_id"] for item in observed_drafts] == [
        "director_npc_conf_prepared"
    ]
    assert "director_npc_conf_introduced" not in {
        item["npc_id"] for item in observation["resources"]["npcs"]
    }


def test_created_setting_mention_in_same_confirmation_is_counted(tmp_path):
    from app.domain.world_setting import WorldSettingStore

    def run_with(scene_text, data_dir):
        stores = StoreBundle(CampaignPaths.from_data_dir(data_dir))
        gateway = _PhaseGateway(
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
                "core_motivation": "保住商队往来北境的路权。",
                "hidden_secret": "北陆商盟暗中资助边境私军。",
                "conflict_style": "先谈利益，谈不拢就断其商路。",
                "social_style": "圆滑而强势。",
                "role": "北境商队执事",
                "faction": None,
                "personality_traits": ["精明", "务实"],
                "known_abilities": ["商事谈判", "押运调度"],
                "appearance": "常穿厚重皮袍。",
                "name": "柴行简",
                "encounter_potentials": ["商路冲突", "会面谈判", "押运同行"],
            },
        )
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            npc_profiles=[],
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


def test_director_skill_specs_cover_every_registered_skill():
    from app.domain.director import DirectorSkillService
    from app.graph.director_skill_specs import get_director_skill_specs

    specs = get_director_skill_specs()
    spec_names = {spec.name for spec in specs}
    assert spec_names == {
        "present_event",
        "introduce_npc",
        "foreshadow_setting",
        "create_npc",
        "create_event",
        "bind_event_actor",
        "enrich_npc",
    }

    service = DirectorSkillService()
    for spec in specs:
        assert spec.description.strip()
        assert spec.phases
        service.register(
            name=spec.name,
            description=spec.description,
            phases=set(spec.phases),
            mutates=spec.mutates,
            arguments_model=spec.arguments_model,
            handler=lambda args, guidance=None: {},
            max_requests_per_batch=spec.max_requests_per_batch,
        )
    assert {spec.name for spec in service.specs_for_phase("generation")} == {
        "present_event",
        "introduce_npc",
        "foreshadow_setting",
    }
    assert {spec.name for spec in service.specs_for_phase("confirmation")} == {
        "create_npc",
        "create_event",
        "bind_event_actor",
        "enrich_npc",
    }
    for spec in specs:
        if "generation" in spec.phases:
            assert spec.mutates is False
        if "confirmation" in spec.phases:
            assert spec.mutates is True


def test_setting_requirements_accepts_exactly_one_entry_with_optional_id():
    from app.graph.director_skill_specs import CreateEventArguments

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
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output(),
    )

    _, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
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
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output(),
    )

    _, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
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

    gateway = _PhaseGateway(
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
            "name": "沈栖舟",
            "role": "北境行商",
            "appearance": "常穿防风皮袍。",
            "personality_traits": ["谨慎", "务实"],
            "known_abilities": ["估价"],
            "faction": "北陆商盟",
            "core_motivation": "守住商队赖以生存的货路。",
            "hidden_secret": "私下追查一批失踪货物。",
            "conflict_style": "先谈条件，再切断补给。",
            "social_style": "言简意赅，重视信用。",
            "encounter_potentials": ["货路求援", "账目争议", "失货调查"],
        },
        event_selection_output=_event_selection_output(),
    )

    _, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
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
    gateway = _PhaseGateway(
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
            "name": "沈栖舟",
            "role": "北境行商",
            "appearance": "常穿防风皮袍。",
            "personality_traits": ["谨慎", "务实"],
            "known_abilities": ["估价"],
            "faction": "北陆商盟",
            "core_motivation": "守住商队赖以生存的货路。",
            "hidden_secret": "私下追查一批失踪货物。",
            "conflict_style": "先谈条件，再切断补给。",
            "social_style": "言简意赅，重视信用。",
            "encounter_potentials": ["货路求援", "账目争议", "失货调查"],
        },
    )

    _, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
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
    gateway = _PhaseGateway(
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
    )

    decision, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
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
    gateway = _PhaseGateway(
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
            "role": "镖师",
            "appearance": "一身劲装。",
            "personality_traits": ["沉稳", "寡言"],
            "known_abilities": ["刀法"],
            "faction": None,
            "core_motivation": "护送商队平安。",
            "hidden_secret": "替人捎带一封密信。",
            "conflict_style": "先护人后对敌。",
            "social_style": "寡言但可靠。",
        },
    )

    with pytest.raises(ValueError, match="enrich_npc requires a newly created npc_id"):
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            npc_profiles=[],
            turn_index=3,
            trace_id="confirmation-director",
            new_npc_profiles=[other_new],
        )


def test_skill_guidance_flows_through_to_execution_handler():
    service = DirectorSkillService()
    seen_guidance: list[str | None] = []

    def handle(arguments: _PresentEventArguments, guidance: str | None = None):
        seen_guidance.append(guidance)
        return {"presented_event_id": arguments.event_id}

    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=_PresentEventArguments,
        handler=handle,
    )
    batch = _decision_batch(
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
    gateway = _PhaseGateway(
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
    )

    decision, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
        turn_index=3,
        trace_id="confirmation-director",
        new_npc_profiles=[new_profile],
    )

    assert decision.requests[0].guidance == "出身行商世家，隐瞒真实货主身份。"
    assert executions.executions[0].guidance == "出身行商世家，隐瞒真实货主身份。"
    backfill_input = gateway.input_data[-1]
    assert backfill_input["director_guidance"] == "出身行商世家，隐瞒真实货主身份。"


def _event_selection_output(actor_roles: list[dict] | None = None) -> dict:
    return {
        "current_story_direction": "一条围绕河岸码头旧案的复仇线。",
        "reason": "世界缺乏明确的长期目标。",
        "title": "沉船账本",
        "duration_phases": 7,
        "actor_roles": actor_roles or [],
        "subject": "码头旧账房",
        "intent": "追回被私吞的账目",
        "method": "暗中盘查往来货船",
        "macro_intent": "旧账房为保全码头世家的账目不择手段。",
        "hook_pool": [
            {
                "hook_id": "hook_rumor",
                "kind": "rumor",
                "content": "码头流传有人在深夜打捞沉船残骸的传闻。",
                "trigger_anchors": ["码头传闻", "河岸码头"],
            }
        ],
        "world_impact": {
            "description": "码头货运被旧账房势力暗中垄断。",
        },
    }


def test_confirmation_agent_can_create_event_with_new_actors(tmp_path):
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
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "控制码头货路"},
            {"actor_key": "actor_2", "role_in_event": "管理隐秘账目"},
        ]),
        npc_output={
            "creation_reason": "码头上的一等把式。",
            "core_motivation": "守住码头的货路。",
            "hidden_secret": "当年沉船时他就在船上。",
            "conflict_style": "先硬扛再周旋。",
            "social_style": "话少，办事利落。",
            "role": "码头把头",
            "faction": "码头会",
            "personality_traits": ["沉稳", "护短"],
            "known_abilities": ["盘货", "相人"],
            "appearance": "左臂有一道旧疤。",
            "name": "沈二爷",
            "encounter_potentials": ["码头冲突", "账本线索", "旧案求助"],
        },
    )

    decision, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

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

    with stores.director_event() as store:
        events = store.list_all()
        assert [event.title for event in events] == ["沉船账本"]
        bound = store.get_by_id(output["event_id"])
        assert bound is not None
        assert bound.deadline_clock == WorldClock(day=2, phase="清晨")
        assert {(actor.ref.kind, actor.ref.resource_id) for actor in bound.actors if actor.ref} == {
            ("npc", npc_id) for npc_id in output["new_npc_ids"]
        }
        assert set(output["actor_names"]) <= {actor.name for actor in bound.actors}
    with stores.director_npc() as store:
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
    with stores.npc_profile() as npc_store:
        existing_id = npc_store.upsert(
            NPCProfile(
                name="陆远舟",
                role="镖师",
                core_motivation="守住商路信誉。",
                hidden_secret="曾替码头世家运过密货。",
            )
        )
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "提供目标与资源"},
            {"actor_key": "actor_2", "role_in_event": "执行沉船调查"},
        ]),
        npc_output={
            "creation_reason": "沉船事件需要一名现场调查者。",
            "core_motivation": "靠查明沉船真相获得立足之地。",
            "hidden_secret": "曾参与伪造一份船货清单。",
            "conflict_style": "先观察再突然行动。",
            "social_style": "谨慎而直接。",
            "role": "沉船调查者",
            "faction": None,
            "personality_traits": ["谨慎", "果断"],
            "known_abilities": ["潜水", "辨认船货"],
            "appearance": "总穿着防水短衣。",
            "name": "闻潮",
            "encounter_potentials": ["沉船调查", "货单争议", "码头冲突"],
        },
    )

    _, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    output = executions.executions[0].output
    assert len(output["new_npc_ids"]) == 1
    with stores.director_event() as event_store:
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
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output(),
    )

    with pytest.raises(
        ValueError,
        match="actor_roles must cover exactly every required actor_key",
    ):
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            npc_profiles=[],
            turn_index=3,
            trace_id="confirmation-director",
        )


def test_create_event_rejects_unknown_existing_actor_before_selection(tmp_path):
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
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "幕后行动"},
        ]),
    )

    with pytest.raises(ValueError, match="unknown active NPC"):
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            npc_profiles=[],
            turn_index=3,
            trace_id="confirmation-director",
        )
    assert gateway.tasks == ["director_agent_decide_confirmation"]


def test_create_event_defers_persistence_when_new_actor_creation_fails(
    tmp_path, monkeypatch
):
    from app.domain.director_npc.creator import DirectorNPCCreator
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
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "控制码头货路"},
            {"actor_key": "actor_2", "role_in_event": "管理隐秘账目"},
        ]),
        npc_output={
            "creation_reason": "码头上的一等把式。",
            "core_motivation": "守住码头的货路。",
            "hidden_secret": "当年沉船时他就在船上。",
            "conflict_style": "先硬扛再周旋。",
            "social_style": "话少，办事利落。",
            "role": "码头把头",
            "faction": "码头会",
            "personality_traits": ["沉稳", "护短"],
            "known_abilities": ["盘货", "相人"],
            "appearance": "左臂有一道旧疤。",
            "name": "沈二爷",
            "encounter_potentials": ["码头冲突", "账本线索", "旧案求助"],
        },
    )

    original_create = DirectorNPCCreator.create
    calls = {"count": 0}

    def flaky_create(self, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise TimeoutError("LLM 超时")
        return original_create(self, **kwargs)

    monkeypatch.setattr(DirectorNPCCreator, "create", flaky_create)

    with pytest.raises(TimeoutError, match="LLM 超时"):
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            npc_profiles=[],
            turn_index=3,
            trace_id="confirmation-director",
        )

    with stores.director_event() as store:
        assert store.list_all() == []
    with stores.director_npc() as store:
        assert store.list_all() == []


def test_wave_b_failure_rolls_back_wave_a_settings_and_all_writes(
    tmp_path, monkeypatch
):
    from app.domain.director_npc.creator import DirectorNPCCreator

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output(),
        setting_output={
            "name": "寒川驿",
            "summary": "连接北境山道和河运的交通节点。",
            "public_facts": ["商旅会在此更换车马。"],
            "hidden_facts": ["地下仓道通往废弃河港。"],
        },
        npc_output={
            "creation_reason": "北境商路需要一个长期活动的人物。",
            "name": "沈栖舟",
            "role": "北境行商",
            "appearance": "常穿防风皮袍。",
            "personality_traits": ["谨慎", "务实"],
            "known_abilities": ["估价"],
            "faction": "北陆商盟",
            "core_motivation": "守住商队赖以生存的货路。",
            "hidden_secret": "私下追查一批失踪货物。",
            "conflict_style": "先谈条件，再切断补给。",
            "social_style": "言简意赅，重视信用。",
            "encounter_potentials": ["货路求援", "账目争议", "失货调查"],
        },
    )

    def flaky_create(self, **kwargs):
        raise TimeoutError("LLM 超时")

    monkeypatch.setattr(DirectorNPCCreator, "create", flaky_create)

    with pytest.raises(TimeoutError, match="LLM 超时"):
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            npc_profiles=[],
            turn_index=3,
            trace_id="confirmation-director",
        )

    with stores.world_setting() as store:
        assert store.list_all() == []
    with stores.director_npc() as store:
        assert store.list_all() == []
    with stores.director_event() as store:
        assert store.list_all() == []


def test_create_event_uses_request_guidance_as_event_direction(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    with stores.world_setting() as store:
        store.save(setting)
        store.save(unrelated)
    gateway = _PhaseGateway(
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
        event_selection_output=_event_selection_output(),
    )

    _, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        npc_profiles=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    assert executions.executions[0].skill == "create_event"
    selector_input = [
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_event_select_ambient"
    ]
    assert selector_input[0]["director_guidance"] == "以船坞翻修风波为整体方向。"
    assert setting.name in selector_input[0]["settings_json"]
    assert unrelated.name not in selector_input[0]["settings_json"]
    assert "director_npc_create" not in gateway.tasks
    with stores.director_event() as store:
        [saved] = store.list_all()
    assert saved.source_setting_ids == [setting.setting_id]


def test_create_event_rejects_more_than_three_new_actors():
    from app.graph.director_skill_specs import CreateEventArguments

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


def test_director_npc_output_rejects_blank_name():
    from app.domain.director_npc.schemas import DirectorNPCCreateOutput

    with pytest.raises(ValidationError):
        DirectorNPCCreateOutput(
            creation_reason="事件需要现场演员。",
            core_motivation="完成受托调查。",
            hidden_secret="隐瞒过去的失败。",
            conflict_style="先观察后行动。",
            social_style="谨慎寡言。",
            role="调查者",
            personality_traits=["谨慎", "果断"],
            appearance="穿着防水短衣。",
            name="   ",
            encounter_potentials=["现场调查", "旧案冲突", "身份暴露"],
        )


def test_director_event_semantic_view_hides_internal_state(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult

    selection = DirectorEventSelectionResult.model_validate(_event_selection_output())
    event = selection.to_event(
        role="arc", turn_index=3, current_clock=WorldClock()
    )
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_event() as store:
        store.save(event)
        saved = store.get_by_id(event.event_id)
    assert saved is not None
    assert saved.hook_pool
    assert saved.world_impact is not None
    assert "hook_pool" not in saved.semantic_view()
    assert "world_impact" not in saved.semantic_view()


def test_director_decide_prompt_defines_create_event_boundary():
    prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    assert "create_event" in prompt
    assert "`setting_requirements`" in prompt
    assert "`create_npc`" in prompt
    assert "`enrich_npc`" in prompt
    assert "source=existing" in prompt
    assert "source=new" in prompt


def test_director_decide_prompt_forbids_quest_wrapping():
    prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    assert "世界设定的绝对依赖性" in prompt
    assert "严禁凭空捏造未备案的专有名词" in prompt
    assert "长草期" in prompt


def test_event_skill_descriptions_forbid_quest_wrapping():
    from app.graph.director_skill_specs import get_director_skill_specs

    specs = {
        spec.name: spec
        for spec in get_director_skill_specs()
        if spec.name in {"create_event"}
    }
    for spec in specs.values():
        assert "主角视线之外" in spec.description
        assert "不得用于推动、包装或代劳主角当前正在执行的主线行动" in spec.description


def test_npc_update_node_keeps_new_profiles_temporary_when_director_runs(
    monkeypatch,
):
    from app.domain.npc_profile.store import NPCProfileStore

    class _Gateway:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs["output_schema"].model_validate(
                {
                    "results": [
                        {
                            "candidate_index": 0,
                            "resolution": "new",
                            "matched_npc_index": None,
                            "persist_alias": False,
                            "turn_observation": None,
                            "profile_patch": None,
                            "new_profile": {
                                "name": "练气巅峰采药女",
                                "aliases": [],
                                "public_summary": "山间采药为生的练气期女子。",
                                "role": "采药女",
                                "appearance": "背一只竹篓，裙角沾着泥。",
                                "personality_traits": ["谨慎", "坚韧"],
                                "known_abilities": ["采药", "辨识药草"],
                                "faction": None,
                                "description": None,
                                "current_location": "药山",
                                "attitude_to_player": None,
                                "current_intent": None,
                            },
                        }
                    ]
                }
            )

    gateway = _Gateway()
    monkeypatch.setattr(confirmation_nodes, "_gateway_for", lambda _state: gateway)
    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, name: lambda: NPCProfileStore(":memory:"),
    )
    state = {
        "write_db": True,
        "npc_extraction": NPCExtractionResult(
            mentions=[
                NPCMention(
                    name="练气巅峰采药女",
                    context_span="药山上有个练气巅峰的采药女。",
                )
            ]
        ),
        "pipeline_input": SimpleNamespace(
            turn_index=3,
            run_director_agent=True,
        ),
        "trace_id": "trace-npc",
        "cache": {},
        "scene_facts_json": '{"scene_focus": "药山。", "facts": []}',
        "updated_state": _current_state(),
        "core_memory": CoreMemory(items=[]),
        "existing_npc_profiles": [],
        "recent_summaries": [],
    }

    result = confirmation_nodes.npc_profile_update_node(state)

    assert result["updated_npc_profile_ids"] == []
    assert len(result["new_npc_profiles"]) == 1
    assert result["new_npc_profiles"][0].name == "练气巅峰采药女"
    assert state["cache"]["new_npc_profiles"][0].name == "练气巅峰采药女"


def test_director_confirm_node_receives_stashed_temporary_profiles(
    monkeypatch,
    tmp_path,
):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    new_profile = NPCProfile(
        name="练气巅峰采药女",
        role="采药女",
        public_summary="山间采药为生的练气期女子。",
    )
    captured = {}

    def fake_run(**kwargs):
        captured["new_npc_profiles"] = kwargs.get("new_npc_profiles")
        return object(), object()

    monkeypatch.setattr(
        confirmation_nodes,
        "run_confirmation_director_agent",
        fake_run,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "_gateway_for",
        lambda _state: object(),
    )
    state = {
        "write_db": True,
        "pipeline_input": SimpleNamespace(
            run_director_agent=True,
            user_input="我在药山。",
            turn_index=3,
        ),
        "cache": {},
        "trace_id": "trace-director",
        "runtime_services": SimpleNamespace(stores=stores),
        "core_memory": CoreMemory(items=[]),
        "updated_state": _current_state(),
        "scene_facts": SceneFactSet(scene_focus="药山。"),
        "effective_scene_text": "药山上的采药女在采摘药草。",
        "recent_summaries": [],
        "selection_summaries": [],
        "npc_extraction": None,
        "existing_npc_profiles": [],
        "new_npc_profiles": [new_profile],
        "node_errors": [],
        "events": [],
    }

    confirmation_nodes.director_agent_confirm_node(state)

    assert captured["new_npc_profiles"] == [new_profile]


def test_generation_agent_projects_pending_engagement_without_writing(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    draft = _director_npc()
    with stores.director_npc() as store:
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
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
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
        current_state=_current_state(),
        user_input="深夜去旧码头看看声音来源。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        pending_engagements=[event_id],
    )

    assert stage.materials[0].kind == "npc"
    assert stage.materials[0].payload.get("source") == "director_npc"
    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"
    with stores.director_npc() as store:
        assert store.get_by_id(draft.draft_id).status == "prepared"


def _review_node_state(
    stores,
    gateway,
    *,
    turn_index=3,
    world_clock: WorldClock | None = None,
):
    updated_state = _current_state()
    if world_clock is not None:
        updated_state.world_clock = world_clock
    return {
        "write_db": True,
        "pipeline_input": SimpleNamespace(
            run_director_agent=True,
            user_input="深夜去旧码头看看。",
            turn_index=turn_index,
        ),
        "cache": {},
        "trace_id": "confirmation-director",
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: gateway,
        ),
        "core_memory": CoreMemory(items=[]),
        "updated_state": updated_state,
        "scene_facts": SceneFactSet(scene_focus="深夜码头。"),
        "recent_summaries": [],
        "events": [],
    }


def _save_deadline_event(stores, *, stage="hidden"):
    from app.domain.director_event.schemas import DirectorEventSelectionResult

    selection = DirectorEventSelectionResult.model_validate(
        _event_selection_output()
    )
    event = selection.to_event(
        role="ambient",
        turn_index=1,
        current_clock=WorldClock(day=1, phase="清晨"),
    )
    with stores.director_event() as store:
        event_id = store.save(event)
        if stage == "engaged":
            store.set_stage(event_id, "engaged", turn_index=2)
    return event_id


def test_hidden_event_uses_original_world_impact_when_world_clock_expires(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    event_id = _save_deadline_event(stores)
    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": "玩家没有接触事件。",
                "action": "keep_hidden",
                "consume_hook_ids": [],
            }],
        },
    )

    confirmation_nodes.director_event_review_node(
        _review_node_state(
            stores,
            gateway,
            world_clock=WorldClock(day=2, phase="清晨"),
        )
    )

    with stores.director_event() as store:
        resolved = store.get_by_id(event_id)
    assert resolved is not None
    assert resolved.stage == "resolved"
    assert resolved.final_world_impact == resolved.world_impact


def test_hidden_event_expires_without_impact_when_player_engages_at_deadline(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    event_id = _save_deadline_event(stores)
    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": "玩家正在调查事件主体。",
                "action": "engage",
                "consume_hook_ids": ["hook_rumor"],
                "player_still_involved": True,
                "final_world_impact": None,
            }],
        },
    )

    confirmation_nodes.director_event_review_node(
        _review_node_state(
            stores,
            gateway,
            world_clock=WorldClock(day=2, phase="清晨"),
        )
    )

    with stores.director_event() as store:
        resolved = store.get_by_id(event_id)
    assert resolved is not None
    assert resolved.stage == "resolved"
    assert resolved.final_world_impact is None


def test_due_engaged_event_exits_without_impact_while_player_is_involved(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    event_id = _save_deadline_event(stores, stage="engaged")
    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": "玩家仍在追查沉船账本。",
                "action": "engage",
                "resolved_reason": None,
                "player_still_involved": True,
                "final_world_impact": None,
            }],
        },
    )

    confirmation_nodes.director_event_review_node(
        _review_node_state(
            stores,
            gateway,
            world_clock=WorldClock(day=2, phase="清晨"),
        )
    )

    with stores.director_event() as store:
        resolved = store.get_by_id(event_id)
    assert resolved is not None
    assert resolved.stage == "resolved"
    assert resolved.final_world_impact is None


def test_due_engaged_event_applies_adjusted_impact_after_player_disengages(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    event_id = _save_deadline_event(stores, stage="engaged")
    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": "玩家已经转向其他目标，但此前破坏了一条货路。",
                "action": "engage",
                "resolved_reason": None,
                "player_still_involved": False,
                "final_world_impact": {
                    "description": "码头垄断仍然形成，但失去一条主要货路。",
                },
            }],
        },
    )

    confirmation_nodes.director_event_review_node(
        _review_node_state(
            stores,
            gateway,
            world_clock=WorldClock(day=2, phase="清晨"),
        )
    )

    with stores.director_event() as store:
        resolved = store.get_by_id(event_id)
    assert resolved is not None
    assert resolved.stage == "resolved"
    assert resolved.final_world_impact is not None
    assert resolved.final_world_impact.description == (
        "码头垄断仍然形成，但失去一条主要货路。"
    )


def test_confirmation_review_event_commits_engagement_and_hook_consumption(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "核心冲突仍在持续。",
                    "action": "keep_hidden",
                    "consume_hook_ids": [],
                }
            ],
        },
    )

    node_state = _review_node_state(stores, gateway)
    confirmation_nodes.director_event_review_node(node_state)

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "核心冲突仍在持续。",
                    "action": "engage",
                    "consume_hook_ids": ["hook_sound"],
                }
            ],
        },
    )

    node_state = _review_node_state(stores, gateway)
    confirmation_nodes.director_event_review_node(node_state)

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "engaged"
        assert {hook.hook_id for hook in persisted.hook_pool if hook.consumed} == {
            "hook_sound"
        }


def test_confirmation_does_not_commit_when_write_db_disabled(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(decision={"reasoning": "无调度。", "requests": []})
    with stores.director_event() as store:
        node_state = {
            "write_db": False,
            "pipeline_input": SimpleNamespace(run_director_agent=True),
            "cache": {},
            "events": [],
        }
        from app.graph import confirmation_nodes

        confirmation_nodes.director_agent_confirm_node(node_state)

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"


def test_review_event_consumes_multiple_hooks_via_arguments(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
            ),
            EventHook(
                hook_id="hook_rumor",
                kind="rumor",
                content="码头流传无灯货船的传闻。",
                trigger_anchors=["旧码头"],
            ),
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "核心冲突仍在持续。",
                "action": "engage",
                "consume_hook_ids": ["hook_sound", "hook_rumor"],
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert {hook.hook_id for hook in persisted.hook_pool if hook.consumed} == {
            "hook_sound",
            "hook_rumor",
        }


def test_generation_presents_persisted_engaged_event(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    with stores.director_event() as store:
        event_id = store.save(selection)
        store.set_stage(event_id, "engaged", turn_index=2)

    gateway = _PhaseGateway(
        decision={
            "reasoning": "继续呈现已接触事件。",
            "requests": [
                {
                    "request_id": "evt_1",
                    "skill": "present_event",
                    "arguments": {"event_id": event_id},
                    "guidance": "作为背景舞台提示呈现。",
                }
            ],
        }
    )

    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="继续在码头巡查。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        pending_engagements=[],
    )

    assert stage.materials[0].kind == "event"
    assert stage.materials[0].payload.get("stage") == "engaged"
    assert "macro_intent" in stage.materials[0].payload
    assert "hooks" not in stage.materials[0].payload


def test_self_check_preserves_engaged_event_ids():
    from app.domain.action.self_check import ActionSelfCheck
    from app.domain.action.schemas import ActionParseResult

    result = ActionParseResult(
        analysis="分析。",
        atomic_actions=[],
        anchors=[],
        engaged_event_ids=["event_1", "event_2"],
    )
    checked = ActionSelfCheck().check(
        result,
        user_input="去码头看看。",
        current_state=_current_state(),
    )
    assert checked.engaged_event_ids == ["event_1", "event_2"]


def test_present_event_rejects_already_consumed_hook(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)
        store.mark_hooks_consumed(event_id, ["hook_sound"], turn_index=2)

    gateway = _PhaseGateway(
        decision={
            "reasoning": "错误地引用已消费 hook。",
            "requests": [
                {
                    "request_id": "evt_1",
                    "skill": "present_event",
                    "arguments": {
                        "event_id": event_id,
                        "hook_id": "hook_sound",
                    },
                    "guidance": "呈现该征兆。",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="consumed hook_id"):
        run_generation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=_current_state(),
            user_input="看看码头有什么动静。",
            context_facts=[],
            recent_summaries=[],
            turn_index=3,
            trace_id="generation-director",
            pending_engagements=[],
        )


def test_present_event_hidden_without_hook_id_presents_nothing(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={
            "reasoning": "无指定 hook。",
            "requests": [
                {
                    "request_id": "evt_1",
                    "skill": "present_event",
                    "arguments": {"event_id": event_id},
                    "guidance": "作为背景呈现。",
                }
            ],
        }
    )

    stage, _ = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="继续在码头巡查。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        pending_engagements=[],
    )

    payload = stage.materials[0].payload
    assert payload["stage"] == "hidden"
    assert "hooks" not in payload
    assert "hook" not in payload
    assert "macro_intent" not in payload


def test_hidden_review_engages_event(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "核心冲突仍在持续。",
                "action": "engage",
                "consume_hook_ids": [],
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "engaged"


def test_review_event_rejects_unknown_consume_hook(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "错误地消费不存在的事件 hook。",
                    "action": "engage",
                    "consume_hook_ids": ["hook_ghost"],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="consume_hook_ids not part of this event"):
        confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))


def test_review_event_rejects_already_consumed_consume_hook(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)
        store.mark_hooks_consumed(event_id, ["hook_sound"], turn_index=2)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "错误地重复消费已消费 hook。",
                    "action": "engage",
                    "consume_hook_ids": ["hook_sound"],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="consume_hook_ids already consumed"):
        confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))


def test_engaged_review_resolves_event(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)
        store.set_stage(event_id, "engaged", turn_index=2)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "猎户撤离码头，冲突收束。",
                "action": "resolve",
                "resolved_reason": "猎户撤离码头。",
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "resolved"
        assert persisted.resolved_turn == 3


def test_engaged_review_keeps_event_when_not_resolved(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)
        store.set_stage(event_id, "engaged", turn_index=2)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "核心冲突仍在持续。",
                "action": "engage",
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "engaged"


def test_review_node_skips_events_created_this_turn(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=3, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "不应被调用。",
                "action": "engage",
                "consume_hook_ids": [],
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"
    assert gateway.tasks == []


def test_keep_hidden_does_not_transition_stage(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "玩家未接触事件。",
                "action": "keep_hidden",
                "consume_hook_ids": [],
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    with stores.director_event() as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"


def test_hidden_review_batch_covers_multiple_events_in_one_call(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    ids = []
    for index, title in enumerate(["无灯船", "夜探码头"], start=1):
        selection = DirectorEventSelectionResult(
            current_story_direction="河岸码头出现异常动静。",
            reason="独立世界事件。",
            title=title,
            duration_phases=7,
            actor_roles=[],
            subject=f"行动方{index}",
            intent="查明灵气异动",
            method="组队搜山",
            macro_intent=f"{title}的动机。",
            hook_pool=[
                EventHook(
                    hook_id=f"hook_{index}",
                    kind="auditory",
                    content=f"{title}的水面异响。",
                    trigger_anchors=["旧码头"],
                )
            ],
            world_impact=WorldImpact(description="外围变为凡人活动区。"),
        ).to_event(
            role="ambient", turn_index=index, current_clock=WorldClock()
        )
        with stores.director_event() as store:
            ids.append(store.save(selection))

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": ids[0],
                    "reason": "事件一未接触。",
                    "action": "keep_hidden",
                    "consume_hook_ids": [],
                },
                {
                    "event_id": ids[1],
                    "reason": "事件二被接触。",
                    "action": "engage",
                    "consume_hook_ids": ["hook_2"],
                },
            ],
        },
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    assert gateway.tasks == ["director_event_review_hidden"]
    with stores.director_event() as store:
        first = store.get_by_id(ids[0])
        second = store.get_by_id(ids[1])
        assert first is not None and first.stage == "hidden"
        assert second is not None and second.stage == "engaged"
        assert {hook.hook_id for hook in second.hook_pool if hook.consumed} == {
            "hook_2"
        }


def test_engaged_review_batch_covers_multiple_events_in_one_call(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    ids = []
    for index, title in enumerate(["无灯船", "夜探码头"], start=1):
        selection = DirectorEventSelectionResult(
            current_story_direction="河岸码头出现异常动静。",
            reason="独立世界事件。",
            title=title,
            duration_phases=7,
            actor_roles=[],
            subject=f"行动方{index}",
            intent="查明灵气异动",
            method="组队搜山",
            macro_intent=f"{title}的动机。",
            hook_pool=[
                EventHook(
                    hook_id=f"hook_{index}",
                    kind="auditory",
                    content=f"{title}的水面异响。",
                    trigger_anchors=["旧码头"],
                )
            ],
            world_impact=WorldImpact(description="外围变为凡人活动区。"),
        ).to_event(
            role="ambient", turn_index=index, current_clock=WorldClock()
        )
        with stores.director_event() as store:
            ids.append(store.save(selection))
            store.set_stage(ids[-1], "engaged", turn_index=index)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": ids[0],
                    "reason": "事件一已收束。",
                    "action": "resolve",
                    "resolved_reason": "行动方撤离。",
                },
                {
                    "event_id": ids[1],
                    "reason": "事件二仍在追踪。",
                    "action": "engage",
                },
            ],
        },
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    assert gateway.tasks == ["director_event_review_engaged"]
    with stores.director_event() as store:
        first = store.get_by_id(ids[0])
        second = store.get_by_id(ids[1])
        assert first is not None and first.stage == "resolved"
        assert second is not None and second.stage == "engaged"



def test_hidden_review_input_includes_hook_trigger_anchors(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
                trigger_anchors=["旧码头", "港务署"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "事件未接触。",
                    "action": "keep_hidden",
                    "consume_hook_ids": [],
                }
            ],
        },
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    assert gateway.tasks == ["director_event_review_hidden"]
    hidden_input = gateway.input_data[0]["events_json"]
    assert '"trigger_anchors"' in hidden_input
    assert "旧码头" in hidden_input
    assert "player_contacts" not in hidden_input
    assert "last_presented_turn" not in hidden_input


def test_review_input_uses_actor_names_only(tmp_path):
    from app.domain.director_event.schemas import (
        DirectorActorRef,
        DirectorEventSelectionResult,
        EventHook,
        WorldImpact,
    )

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
        actors=[DirectorActorRef(name="港务署"), DirectorActorRef(name="林澈")],
    )
    with stores.director_event() as store:
        event_id = store.save(selection)

    gateway = _PhaseGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "事件未接触。",
                    "action": "keep_hidden",
                    "consume_hook_ids": [],
                }
            ],
        },
    )

    confirmation_nodes.director_event_review_node(_review_node_state(stores, gateway))

    hidden_input = gateway.input_data[0]["events_json"]
    assert '"港务署"' in hidden_input
    assert '"林澈"' in hidden_input
