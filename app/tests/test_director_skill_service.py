import json
import pytest

import threading
from pydantic import ValidationError
from app.core.llm import MockLLM
from app.core.llm.errors import LLMProviderError
from app.domain.director import DirectorAgent
from app.domain.director import DirectorSkillRequest
from app.domain.director import DirectorSkillService

from app.tests.factories.director import (
    make_decision_batch,
    make_director_observation,
)
from app.tests.factories.director_schemas import (
    SkillCreateEventArguments,
    SkillCreateNPCArguments,
    SkillEventSelectionArguments,
)


def test_skill_service_exposes_only_skills_available_in_phase():
    service = DirectorSkillService()
    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation", "confirmation"},
        mutates=False,
        arguments_model=SkillEventSelectionArguments,
        handler=lambda arguments, guidance=None: {"event_id": arguments.event_id},
    )
    service.register(
        name="create_event",
        description="创建新的导演事件。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=SkillCreateEventArguments,
        handler=lambda arguments, guidance=None: {"title": arguments.title},
    )

    assert [
        spec.name for spec in service.specs_for_phase("generation")
    ] == ["present_event"]
    assert [
        spec.name for spec in service.specs_for_phase("confirmation")
    ] == ["present_event", "create_event"]


















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
        arguments_model=SkillEventSelectionArguments,
        handler=lambda arguments, guidance=None: None,
        arguments_validator=validate_event,
    )
    duplicate = make_decision_batch(
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

    unknown = make_decision_batch(
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




def test_structured_guidance_forwards_name_purpose_and_discards_description():
    from app.domain.director import DirectorGuidance

    service = DirectorSkillService()
    service.register(
        name="create_npc",
        description="创建新的导演 NPC。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=SkillCreateNPCArguments,
        handler=lambda arguments, guidance=None: {"name": arguments.name},
    )
    batch = make_decision_batch(
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








def test_director_agent_uses_registered_skill_and_validates_selection():
    service = DirectorSkillService()
    service.register(
        name="present_event",
        description="让已有事件进入本轮舞台。",
        phases={"generation"},
        mutates=False,
        arguments_model=SkillEventSelectionArguments,
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
        make_director_observation(),
        trace_id="director-agent-test",
    )

    assert result.requests[0].skill == "present_event"
    assert result.requests[0].arguments == {"event_id": "event_1"}






def test_batch_validation_surfaces_real_arguments_error():
    from app.domain.director.agent import _batch_valid
    from app.domain.director.skill_specs import CreateEventArguments

    service = DirectorSkillService()
    service.register(
        name="create_event",
        description="创建新的导演事件。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=CreateEventArguments,
        handler=lambda arguments, guidance=None: {},
    )
    request = DirectorSkillRequest(
        request_id="event_1",
        skill="create_event",
        arguments={
            "setting_requirements": [
                {"kind": "location", "setting_description": "依托山道驿站。"},
            ],
            "actors": [],
        },
        guidance="建立独立环境事件。",
    )

    valid, message = _batch_valid(service, [request], "confirmation")

    assert valid is False
    assert message is not None
    assert "at least one real actor" in message
