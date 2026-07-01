import json
from pathlib import Path

import pytest

from app.core.llm import MockLLM
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, ResourcesState, SceneState
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.roll.schemas import RollResult, RollResultBatch
from app.domain.roll_modifier.schemas import RollModifier
from app.domain.turn_resolution import (
    ActionResolutionFrame,
    TurnResolutionAgent,
    build_roll_judgments,
    cooldown_filter_types,
    map_roll_to_band,
    roll_filter_types,
)
from app.workflows.resolve_turn import resolve_turn_workflow


def _action(
    action_id: str = "a1",
    action_type: str = "observe",
    risk_level: str | None = None,
) -> AtomicAction:
    return AtomicAction(
        action_id=action_id,
        sequence=1,
        actor="主角",
        action_type=action_type,
        raw_text="观察四周",
        verb="观察",
        target="四周",
        likely_needs_roll=True,
        roll_hint="perception",
        risk_level=risk_level,
        confidence=0.9,
    )


def _parse(action: AtomicAction | None = None) -> ActionParseResult:
    return ActionParseResult(
        turn_intent="观察",
        atomic_actions=[action or _action()],
    )


def _roll(value: int = 20, action_id: str = "a1") -> RollResult:
    return RollResult(
        action_id=action_id,
        sequence=1,
        actor="主角",
        action_type="observe",
        dice="1d20",
        value=value,
        dice_sides=20,
        roll_hint="perception",
    )


def _roll_batch(*rolls: RollResult) -> RollResultBatch:
    return RollResultBatch(
        dice_formula=f"{len(rolls)}d20",
        dice_sides=20,
        total_rolls=len(rolls),
        results=list(rolls),
    )


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="北郡"),
        player=PlayerState(name="卢西安"),
        resources=ResourcesState(),
    )


def _response(selected_type: str = "advance", outcome_band: str = "critical_success") -> str:
    return json.dumps({
        "resolution_reasoning": "本回合只有观察行动，roll_judgment 为给定 outcome_band，观察四周属于 A 类可能。CurrentState 支持主角处在当前场景中，因此结果应兑现为发现环境异常，并选择与推进调查相符的叙事类型。",
        "selected_type": selected_type,
        "action_resolutions": [
            {
                "action_id": "a1",
                "effect_reasoning": "支点：当前场景可观察；边界：只发现环境异常和可用优势。",
                "resolved_fact": "主角发现四周最关键的异常，并获得一个可立即利用的优势。",
            }
        ],
    }, ensure_ascii=False)


class _SpyGateway:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


class TestRollJudgment:
    def test_roll_band_mapping_stays_programmatic(self):
        assert map_roll_to_band(1, 20) == "critical_failure"
        assert map_roll_to_band(20, 20) == "critical_success"
        assert map_roll_to_band(10, 20) == "partial_success"

    def test_build_roll_judgments_uses_adjusted_value(self):
        roll = RollResult(
            action_id="a1",
            sequence=1,
            actor="主角",
            action_type="observe",
            dice="1d20",
            value=12,
            dice_sides=20,
            raw_value=7,
            modifiers=[
                RollModifier(
                    action_id="a1",
                    label="清晰视野",
                    reason="环境提供明确观察优势。",
                    value=5,
                )
            ],
        )

        judgments = build_roll_judgments(_parse(), _roll_batch(roll))

        assert judgments[0].roll_value == 12
        assert judgments[0].raw_value == 7
        assert judgments[0].outcome_band == "partial_success"

    def test_action_resolution_fact_is_length_limited(self):
        with pytest.raises(ValueError):
            ActionResolutionFrame(
                action_id="a1",
                sequence=1,
                actor="主角",
                action_type="observe",
                outcome_band="success",
                effect_reasoning="支点：当前场景可观察；边界：只给克制发现。",
                resolved_fact="超长事实边界" * 25,
            )


class TestTurnResolutionFilters:
    def test_critical_failure_only_allows_pressure_or_complication(self):
        judgments = build_roll_judgments(_parse(), _roll_batch(_roll(1)))

        available, _ = roll_filter_types(judgments)

        assert available == ["escalate_pressure", "complication"]

    def test_no_failure_like_removes_pressure_and_complication(self):
        judgments = build_roll_judgments(_parse(), _roll_batch(_roll(20)))

        available, _ = roll_filter_types(judgments)

        assert "escalate_pressure" not in available
        assert "complication" not in available

    def test_cooldown_blocks_repeated_non_advance_type(self):
        available, _ = cooldown_filter_types(
            ["advance", "relationship_shift"],
            ["relationship_shift"],
        )

        assert available == ["advance"]


class TestTurnResolutionAgent:
    def test_prompt_contains_top_level_reasoning_requirements(self):
        prompt = Path("app/prompts/turn_resolution_v1.md").read_text(encoding="utf-8")

        assert "resolution_reasoning" in prompt
        assert "回合判定前的逻辑审计" in prompt
        assert "结果的合理表现是什么" in prompt
        assert "确定信息边界" in prompt
        assert "客观可能性" in prompt
        assert "selected_type 必须来自" in prompt
        assert "本行动的结果依据" in prompt
        assert "resolved_fact" in prompt
        assert "本行动判定后的权威事实结论" in prompt
        assert "不能写成具体的细节描写" in prompt
        assert "resolved_effect" not in prompt

    def test_prompt_input_omits_action_parse_result(self):
        gateway = _SpyGateway(json.loads(_response("advance", "critical_success")))

        TurnResolutionAgent(gateway).resolve(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse(),
            roll_results=_roll_batch(_roll(20)),
            core_memory=CoreMemory(items=[]),
            retrieved_memories=CandidateRetrievalResult(),
            scene_summaries=[],
        )

        input_data = gateway.calls[0]["input_data"]
        assert "user_input" in input_data
        assert "roll_judgments_json" in input_data
        assert "action_parse_result_json" not in input_data
        assert "resolution_reasoning" in input_data["output_schema_json"]
        schema = json.loads(input_data["output_schema_json"])
        action_resolution_ref = schema["properties"]["action_resolutions"]["items"]["$ref"]
        action_resolution_def = schema["$defs"][action_resolution_ref.removeprefix("#/$defs/")]
        action_resolution_fields = action_resolution_def["properties"]
        assert set(action_resolution_fields) == {
            "action_id",
            "effect_reasoning",
            "resolved_fact",
        }

    def test_llm_interprets_consequence_and_selects_type(self):
        mock = MockLLM()
        mock.set_responses([_response("advance", "critical_success")])

        result = TurnResolutionAgent(mock.build_gateway()).resolve(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse(),
            roll_results=_roll_batch(_roll(20)),
            core_memory=CoreMemory(items=[]),
            retrieved_memories=CandidateRetrievalResult(),
            scene_summaries=[],
        )

        assert result.selected_type == "advance"
        assert "观察行动" in result.resolution_reasoning
        assert result.action_resolutions[0].outcome_band == "critical_success"
        assert "异常" in result.action_resolutions[0].resolved_fact

    def test_fills_action_resolution_identity_from_roll_judgment(self):
        mock = MockLLM()
        mock.set_responses([_response("advance", "failure")])

        result = TurnResolutionAgent(mock.build_gateway()).resolve(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse(),
            roll_results=_roll_batch(_roll(20)),
            core_memory=CoreMemory(items=[]),
            retrieved_memories=CandidateRetrievalResult(),
            scene_summaries=[],
        )

        frame = result.action_resolutions[0]
        assert frame.sequence == 1
        assert frame.actor == "主角"
        assert frame.action_type == "observe"
        assert frame.outcome_band == "critical_success"

    def test_workflow_returns_result(self):
        mock = MockLLM()
        mock.set_responses([_response("advance", "critical_success")])

        result = resolve_turn_workflow(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse(),
            roll_results=_roll_batch(_roll(20)),
            core_memory=CoreMemory(items=[]),
            retrieved_memories=CandidateRetrievalResult(),
            scene_summaries=[],
            gateway=mock.build_gateway(),
        )

        assert result.total_resolutions == 1
