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
from app.domain.current_state import CurrentState, PlayerState, SceneState
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
        name="create_coupled_scene",
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
    ] == ["present_event", "create_coupled_scene"]


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
    batch = DirectorDecisionBatch(
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
        match="generation director skills require guidance",
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
    batch = DirectorDecisionBatch(
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

    invalid_batch = DirectorDecisionBatch(
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
    duplicate = DirectorDecisionBatch(
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

    unknown = DirectorDecisionBatch(
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
        name="create_coupled_scene",
        description="创建新的导演事件。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=_CreateEventArguments,
        handler=lambda arguments, guidance=None: {"title": arguments.title},
    )
    distinct = DirectorDecisionBatch(
        reasoning="两次同参数但方向不同的创建。",
        requests=[
            DirectorSkillRequest(
                request_id="one",
                skill="create_coupled_scene",
                arguments={"title": "码头暗涌"},
                guidance="码头势力正在暗中重整旗鼓。",
            ),
            DirectorSkillRequest(
                request_id="two",
                skill="create_coupled_scene",
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

    duplicated = DirectorDecisionBatch(
        reasoning="同技能同参数且 guidance 相同，视为重复。",
        requests=[
            DirectorSkillRequest(
                request_id="one",
                skill="create_coupled_scene",
                arguments={"title": "码头暗涌"},
                guidance="码头势力正在暗中重整旗鼓。",
            ),
            DirectorSkillRequest(
                request_id="two",
                skill="create_coupled_scene",
                arguments={"title": "码头暗涌"},
                guidance="码头势力正在暗中重整旗鼓。",
            ),
        ],
    )
    with pytest.raises(ValueError, match="duplicate director skill request"):
        service.validate_batch(duplicated, phase="confirmation")


def test_director_agent_returns_empty_batch_without_available_skills():
    mock = MockLLM()
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

    assert "生成阶段只能提出非持久化的舞台调度" in generation_prompt
    assert "`view_mode`" in generation_prompt
    assert "create_coupled_scene" not in generation_prompt

    assert "主动造物的导演特权" in confirmation_prompt
    assert "`create_coupled_scene`" in confirmation_prompt
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
    ):
        self.decision = decision
        self.npc_output = npc_output
        self.backfill_output = backfill_output
        self.event_selection_output = event_selection_output
        self.event_review_output = event_review_output
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
            payload = self.decision
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


def test_confirmation_agent_can_create_npc_without_setting(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = _PhaseGateway(
        decision={
            "reasoning": "需要准备一个长期社区人物。",
            "requests": [
                {
                    "request_id": "create_1",
                    "skill": "create_npc",
                    "arguments": {
                        "anchors": ["河岸社区"],
                    },
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

    decision, executions = run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        entity_profiles=[],
        npc_profiles=[],
        entity_mentions=[],
        npc_mentions=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    assert decision.requests[0].skill == "create_npc"
    assert executions.executions[0].output["name"] == "林澈"
    assert gateway.tasks == ["director_agent_decide_confirmation", "director_npc_create"]
    with stores.director_npc() as store:
        [saved] = store.list_prepared()
    assert saved.creation_anchors == ["河岸社区"]
    with stores.world_setting() as store:
        assert store.list_all() == []


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
        "create_coupled_scene",
        "enrich_setting",
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
        )
    assert {spec.name for spec in service.specs_for_phase("generation")} == {
        "present_event",
        "introduce_npc",
        "foreshadow_setting",
    }
    assert {spec.name for spec in service.specs_for_phase("confirmation")} == {
        "create_npc",
        "create_coupled_scene",
        "enrich_setting",
        "bind_event_actor",
        "enrich_npc",
    }
    for spec in specs:
        if "generation" in spec.phases:
            assert spec.mutates is False
        if "confirmation" in spec.phases:
            assert spec.mutates is True


def test_confirmation_agent_can_enrich_new_npc_via_skill(tmp_path):
    import json

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
                    "arguments": {"npc_id": new_id},
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
        entity_profiles=[],
        npc_profiles=[],
        entity_mentions=[],
        npc_mentions=[],
        turn_index=3,
        trace_id="confirmation-director",
        new_npc_profiles=[new_profile],
    )

    assert decision.requests[0].skill == "enrich_npc"
    assert executions.executions[0].output["name"] == "苏锦屏"
    assert gateway.tasks == ["director_agent_decide_confirmation", "npc_profile_backfill"]
    creative_brief = json.loads(
        gateway.input_data[1]["creative_brief_json"]
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


def test_confirmation_agent_rejects_enrich_npc_for_existing_profile(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
                    "arguments": {"npc_id": existing_id},
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
            entity_profiles=[],
            npc_profiles=[],
            entity_mentions=[],
            npc_mentions=[],
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
    batch = DirectorDecisionBatch(
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
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
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
                    "arguments": {"npc_id": new_id},
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
        entity_profiles=[],
        npc_profiles=[],
        entity_mentions=[],
        npc_mentions=[],
        turn_index=3,
        trace_id="confirmation-director",
        new_npc_profiles=[new_profile],
    )

    assert decision.requests[0].guidance == "出身行商世家，隐瞒真实货主身份。"
    assert executions.executions[0].guidance == "出身行商世家，隐瞒真实货主身份。"
    backfill_input = gateway.input_data[-1]
    assert backfill_input["director_guidance"] == "出身行商世家，隐瞒真实货主身份。"


def _event_selection_output() -> dict:
    return {
        "current_story_direction": "一条围绕河岸码头旧案的复仇线。",
        "reason": "世界缺乏明确的长期目标。",
        "title": "沉船账本",
        "actors": ["码头苦力"],
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


def test_confirmation_agent_can_create_coupled_scene_via_skill(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = _PhaseGateway(
        decision={
            "reasoning": "需要同时孕育一个事件与它的核心班底。",
            "requests": [
                {
                    "request_id": "coupled_1",
                    "skill": "create_coupled_scene",
                    "arguments": {
                        "role": "arc",
                        "npc_count": 2,
                        "event_guidance": "码头货运世家的旧账成为导火索。",
                        "npc_guidances": [
                            "码头把头，世代守着货路。",
                            "账房先生，暗中替世家管账。",
                        ],
                    },
                    "guidance": "以码头货运世家为核心演员班底。",
                }
            ],
        },
        event_selection_output=_event_selection_output(),
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
        entity_profiles=[],
        npc_profiles=[],
        entity_mentions=[],
        npc_mentions=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    assert executions.executions[0].skill == "create_coupled_scene"
    output = executions.executions[0].output
    assert output["role"] == "arc"
    assert len(output["npc_ids"]) == 2
    assert len(output["npc_names"]) == 2
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
        assert {(actor.ref.kind, actor.ref.resource_id) for actor in bound.actors if actor.ref} == {
            ("npc", npc_id) for npc_id in output["npc_ids"]
        }
        assert set(output["npc_names"]) <= {actor.name for actor in bound.actors}
    with stores.director_npc() as store:
        prepared = store.list_prepared()
        assert {draft.draft_id for draft in prepared} == set(output["npc_ids"])

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
    assert create_calls[0]["director_guidance"] == "码头把头，世代守着货路。"
    assert create_calls[1]["director_guidance"] == "账房先生，暗中替世家管账。"

    selector_input = [
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_event_select_arc"
    ]
    assert selector_input[0]["director_guidance"] == "码头货运世家的旧账成为导火索。"


def test_coupled_scene_defers_persistence_when_npc_creation_fails(
    tmp_path, monkeypatch
):
    from app.domain.director_npc.creator import DirectorNPCCreator

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = _PhaseGateway(
        decision={
            "reasoning": "需要同时孕育一个事件与它的核心班底。",
            "requests": [
                {
                    "request_id": "coupled_1",
                    "skill": "create_coupled_scene",
                    "arguments": {
                        "role": "arc",
                        "npc_count": 2,
                        "event_guidance": "码头货运世家的旧账成为导火索。",
                        "npc_guidances": [
                            "码头把头，世代守着货路。",
                            "账房先生，暗中替世家管账。",
                        ],
                    },
                    "guidance": "以码头货运世家为核心演员班底。",
                }
            ],
        },
        event_selection_output=_event_selection_output(),
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
            entity_profiles=[],
            npc_profiles=[],
            entity_mentions=[],
            npc_mentions=[],
            turn_index=3,
            trace_id="confirmation-director",
        )

    with stores.director_event() as store:
        assert store.list_all() == []
    with stores.director_npc() as store:
        assert store.list_all() == []


def test_coupled_scene_guidance_falls_back_to_request_guidance(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = _PhaseGateway(
        decision={
            "reasoning": "需要孕育事件班底。",
            "requests": [
                {
                    "request_id": "coupled_1",
                    "skill": "create_coupled_scene",
                    "arguments": {"role": "ambient", "npc_count": 1},
                    "guidance": "以船坞翻修风波为整体方向。",
                }
            ],
        },
        event_selection_output=_event_selection_output(),
        npc_output={
            "creation_reason": "船坞里的一把好手。",
            "core_motivation": "保住船坞的饭碗。",
            "hidden_secret": "曾替人藏过一批货。",
            "conflict_style": "先动手再讲理。",
            "social_style": "嗓门大。",
            "role": "船坞工头",
            "faction": "船坞行会",
            "personality_traits": ["爽利", "护短"],
            "known_abilities": ["修船", "排班"],
            "appearance": "满手老茧。",
            "name": "赵铁柱",
            "encounter_potentials": ["船坞冲突", "藏货旧案", "工钱纷争"],
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
        entity_profiles=[],
        npc_profiles=[],
        entity_mentions=[],
        npc_mentions=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    assert executions.executions[0].skill == "create_coupled_scene"
    selector_input = [
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_event_select_ambient"
    ]
    assert selector_input[0]["director_guidance"] == "以船坞翻修风波为整体方向。"
    create_calls = [
        entry
        for entry, task in zip(gateway.input_data, gateway.tasks)
        if task == "director_npc_create"
    ]
    assert create_calls[0]["director_guidance"] == "以船坞翻修风波为整体方向。"


def test_coupled_scene_rejects_oversized_npc_guidances():
    from app.graph.director_skill_specs import CreateCoupledSceneArguments

    with pytest.raises(ValidationError):
        CreateCoupledSceneArguments(
            role="arc",
            npc_count=1,
            npc_guidances=["方向一", "方向二"],
        )


def test_director_event_semantic_view_hides_internal_state(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult

    selection = DirectorEventSelectionResult.model_validate(_event_selection_output())
    event = selection.to_event(role="arc", turn_index=3)
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_event() as store:
        store.save(event)
        saved = store.get_by_id(event.event_id)
    assert saved is not None
    assert saved.hook_pool
    assert saved.world_impact is not None
    assert "hook_pool" not in saved.semantic_view()
    assert "world_impact" not in saved.semantic_view()


def test_director_decide_prompt_defines_coupled_scene_boundary():
    prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    assert "create_coupled_scene" in prompt
    assert "唯一技能" in prompt
    assert "`create_npc`" in prompt
    assert "绝不可强迫主角参与" in prompt
    assert "`event_guidance`" in prompt
    assert "`npc_guidances`" in prompt


def test_director_decide_prompt_forbids_quest_wrapping():
    prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    assert "主角视线之外的平行运转" in prompt
    assert "绝不可强迫主角参与" in prompt
    assert "也不可代劳主角的工作" in prompt
    assert "长草期" in prompt


def test_event_skill_descriptions_forbid_quest_wrapping():
    from app.graph.director_skill_specs import get_director_skill_specs

    specs = {
        spec.name: spec
        for spec in get_director_skill_specs()
        if spec.name in {"create_coupled_scene"}
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
        "recent_summaries": [],
        "selection_summaries": [],
        "entity_extraction": None,
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
        actors=["港务署"],
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


def _review_node_state(stores, gateway, *, turn_index=3):
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
        "updated_state": _current_state(),
        "scene_facts": SceneFactSet(scene_focus="深夜码头。"),
        "recent_summaries": [],
        "events": [],
    }


def test_confirmation_review_event_commits_engagement_and_hook_consumption(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
        title="无灯船",
        actors=["港务署"],
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=3)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
            actors=["港务署"],
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
        ).to_event(role="ambient", turn_index=index)
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
            actors=["港务署"],
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
        ).to_event(role="ambient", turn_index=index)
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
        actors=["港务署"],
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
    ).to_event(role="ambient", turn_index=1)
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
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
        title="无灯船",
        actors=["港务署", "林澈"],
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
    ).to_event(role="ambient", turn_index=1)
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
