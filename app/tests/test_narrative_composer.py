import json

import pytest

from app.core.llm.gateway import LLMGateway
from app.core.llm.prompts import PromptLoader
from app.core.llm.providers import MockProvider
from app.core.llm.schema import schema_json
from app.core.llm.tracing import NullTraceLogger
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.narrative_composer.runner import (
    _HardComposerResult,
    run_narrative_composition,
)
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.action_judgment.schemas import ActionJudgment


def _context() -> NarrativeComposerContext:
    return NarrativeComposerContext(
        user_input="观察四周",
        current_state=CurrentState(
            scene=SceneState(scene_id="s1", location="广场", participants=["萧欢"]),
            player=PlayerState(name="萧欢"),
        ),
        action_judgments=[ActionJudgment(
            action_id="A1",
            sequence=1,
            actor=["萧欢"],
            action_type="observe",
            action_text="观察四周",
            resolution_source="automatic",
            outcome_band="success",
        )],
        core_memory=CoreMemory(items=[]),
    )


def _gateway(output: dict) -> LLMGateway:
    provider = MockProvider()
    provider.set_responses([json.dumps(output, ensure_ascii=False)] * 3)
    return LLMGateway(
        provider=provider,
        prompt_loader=PromptLoader(),
        tracer=NullTraceLogger(),
    )


def _output() -> dict:
    return {
        "director_reason": None,
        "action_resolutions": [{
            "action_id": "A1",
            "outcome_band": "success",
            "resolved_fact": "你看清了广场周围的动静。",
        }],
        "scene_stopping_point": "广场仍有数处动静等待回应。",
        "planned_events": [{
            "event_id": "e1",
            "event_fact": "你观察广场，周围的人群作出反应。",
            "linked_outcome_ids": ["A1"],
            "involved_entities": [],
        }],
        "scene_reasoning": "落实成功观察并保持局面开放。",
        "scene_text": "你环顾广场，人群的动静逐一落入眼中。",
        "closing_prompt": "人群仍在流动，你会怎么做？",
        "next_action_directions": [],
    }


def test_prompt_schema_keeps_hard_limit_and_outcome_enum():
    schema = json.loads(schema_json(_HardComposerResult))
    action = next(
        item for item in schema["$defs"].values()
        if "resolved_fact" in item.get("properties", {})
    )
    assert action["properties"]["resolved_fact"]["maxLength"] == 80
    assert "failure" in action["properties"]["outcome_band"]["enum"]


def test_composer_accepts_matching_program_judgment():
    result = run_narrative_composition(_gateway(_output()), context=_context())
    assert result.action_resolutions[0].outcome_band == "success"
    assert result.scene_text.startswith("你环顾广场")


def test_composer_rejects_outcome_reversal():
    output = _output()
    output["action_resolutions"][0]["outcome_band"] = "failure"
    with pytest.raises(Exception, match="outcome_band mismatch"):
        run_narrative_composition(_gateway(output), context=_context())
