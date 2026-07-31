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
        handler=lambda arguments: {"event_id": arguments.event_id},
    )
    service.register(
        name="create_event",
        description="创建新的导演事件。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=_CreateEventArguments,
        handler=lambda arguments: {"title": arguments.title},
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
            handler=lambda arguments: None,
        )


def test_skill_service_validates_arguments_and_executes_in_request_order():
    service = DirectorSkillService()
    called: list[str] = []

    def handle(arguments: _PresentEventArguments):
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
            ),
            DirectorSkillRequest(
                request_id="request_2",
                skill="present_event",
                arguments={"event_id": "event_b"},
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
        handler=lambda arguments: None,
        arguments_validator=validate_event,
    )
    duplicate = DirectorDecisionBatch(
        reasoning="错误地重复请求。",
        requests=[
            DirectorSkillRequest(
                request_id="one",
                skill="present_event",
                arguments={"event_id": "event_1"},
            ),
            DirectorSkillRequest(
                request_id="two",
                skill="present_event",
                arguments={"event_id": "event_1"},
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
            ),
        ],
    )
    with pytest.raises(ValueError, match="unknown event"):
        service.validate_batch(unknown, phase="generation")


def test_director_agent_returns_empty_batch_without_available_skills():
    mock = MockLLM()
    agent = DirectorAgent(mock.build_gateway(), DirectorSkillService())

    result = agent.decide(_observation())

    assert result.requests == []


def test_director_agent_uses_registered_skill_and_validates_selection():
    service = DirectorSkillService()
    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=_PresentEventArguments,
        handler=lambda arguments: None,
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
              "arguments": {"event_id": "event_1"}
            }
          ]
        }
        """
    ])

    result = DirectorAgent(mock.build_gateway(), service).decide(
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
        handler=lambda arguments: None,
    )
    mock = MockLLM()
    invalid_response = """
        {
          "reasoning": "错误地选择了未开放的技能。",
          "requests": [
            {
              "request_id": "request_1",
              "skill": "create_event",
              "arguments": {"title": "错误事件"}
            }
          ]
        }
        """
    mock.set_responses([invalid_response, invalid_response, invalid_response])

    with pytest.raises(
        LLMOutputValidationError,
        match="unknown director skill: create_event",
    ):
        DirectorAgent(mock.build_gateway(), service).decide(_observation())


def test_director_agent_prompt_is_chinese():
    prompt, _ = PromptLoader().load("director_agent_decide")

    assert "导演协调 Agent" in prompt
    assert "只输出符合 Schema 的 JSON" in prompt


class _PhaseGateway:
    def __init__(self, *, decision: dict, npc_output: dict | None = None):
        self.decision = decision
        self.npc_output = npc_output
        self.tasks: list[str] = []

    def run(self, **kwargs):
        task = kwargs["task"]
        self.tasks.append(task)
        if task == "director_agent_decide":
            payload = self.decision
        elif task == "director_npc_create" and self.npc_output is not None:
            payload = self.npc_output
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
                        "reason": "玩家正在社区诊所附近行动。",
                    },
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
    assert stage.materials[0].payload["source"] == "director_npc"
    with stores.director_npc() as store:
        assert store.get_by_id(draft.draft_id).status == "prepared"


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
                        "purpose": "补充社区医疗与旧案联系。",
                        "anchors": ["河岸社区"],
                    },
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
    assert gateway.tasks == ["director_agent_decide", "director_npc_create"]
    with stores.director_npc() as store:
        [saved] = store.list_prepared()
    assert saved.creation_anchors == ["河岸社区"]
    with stores.world_setting() as store:
        assert store.list_all() == []
