import pytest

import threading
from pydantic import ValidationError
from app.core.llm import LLMOutputValidationError
from app.core.llm import MockLLM
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
            arguments_model=SkillCreateEventArguments,
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
        arguments_model=SkillCreateNPCArguments,
        handler=handle,
    )
    batch = make_decision_batch(
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
        arguments_model=SkillEventSelectionArguments,
        handler=lambda arguments, guidance=None: None,
    )
    batch = make_decision_batch(
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
    from app.domain.director.skill_specs import IntroduceNPCArguments

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

    def handle(arguments: SkillEventSelectionArguments, guidance: str | None = None):
        called.append(arguments.event_id)
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

    invalid_batch = make_decision_batch(
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


def test_skill_service_treats_guidance_as_part_of_request_uniqueness():
    service = DirectorSkillService()
    service.register(
        name="create_event",
        description="创建新的导演事件。",
        phases={"confirmation"},
        mutates=True,
        arguments_model=SkillCreateEventArguments,
        handler=lambda arguments, guidance=None: {"title": arguments.title},
    )
    distinct = make_decision_batch(
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

    duplicated = make_decision_batch(
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


def test_structured_guidance_dedup_ignores_description_honeypot():
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
    duplicate = make_decision_batch(
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
        arguments_model=SkillCreateEventArguments,
        handler=lambda arguments, guidance=None: {"title": arguments.title},
    )
    batch = make_decision_batch(
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

    result = agent.decide_generation(make_director_observation())

    assert result.requests == []


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


def test_director_agent_rejects_unregistered_skill_selected_by_llm():
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
        DirectorAgent(mock.build_gateway(), service).decide_generation(make_director_observation())


def test_director_skill_specs_cover_every_registered_skill():
    from app.domain.director import DirectorSkillService
    from app.domain.director.skill_specs import get_director_skill_specs

    specs = get_director_skill_specs()
    spec_names = {spec.name for spec in specs}
    assert spec_names == {
        "present_event",
        "introduce_npc",
        "foreshadow_setting",
        "intrude_engaged_event",
        "create_npc",
        "create_event",
        "bind_event_actor",
        "enrich_npc",
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
        "foreshadow_setting",
        "intrude_engaged_event",
        "upgrade_disclosure",
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
