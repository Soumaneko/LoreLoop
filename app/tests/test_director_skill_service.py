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




def test_director_skill_specs_cover_every_registered_skill():
    from app.domain.director import DirectorSkillService
    from app.domain.director.skill_specs import get_director_skill_specs

    specs = get_director_skill_specs()
    spec_names = {spec.name for spec in specs}
    assert spec_names == {
        "present_event",
        "introduce_npc",
        "intrude_engaged_event",
        "create_npc",
        "create_event",
        "bind_event_actor",
        "upgrade_disclosure",
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
        "intrude_engaged_event",
        "upgrade_disclosure",
    }
    assert {spec.name for spec in service.specs_for_phase("confirmation")} == {
        "create_npc",
        "create_event",
        "bind_event_actor",
    }
    for spec in specs:
        if "generation" in spec.phases:
            assert spec.mutates is False
        if "confirmation" in spec.phases:
            assert spec.mutates is True
    create_event = next(spec for spec in specs if spec.name == "create_event")
    assert create_event.max_requests_per_batch == 1
