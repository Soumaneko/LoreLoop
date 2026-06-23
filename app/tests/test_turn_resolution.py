import json

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
        "selected_type": selected_type,
        "action_resolutions": [
            {
                "action_id": "a1",
                "sequence": 1,
                "actor": "主角",
                "action_type": "observe",
                "outcome_band": outcome_band,
                "resolved_effect": "主角清楚捕捉到四周最关键的异常，并获得一个可立即利用的优势。",
            }
        ],
    }, ensure_ascii=False)


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

    def test_action_resolution_effect_is_length_limited(self):
        with pytest.raises(ValueError):
            ActionResolutionFrame(
                action_id="a1",
                sequence=1,
                actor="主角",
                action_type="observe",
                outcome_band="success",
                resolved_effect=(
                    "这是一段过长的结果解释，会开始描写角色动作、场景氛围、心理变化和后续走向，"
                    "因此它已经不再是简短的判定事实，而是重新变成了大纲的大纲，甚至还继续补充更多细节，"
                    "包括每个角色的站位、眼神、动作、下一步安排和场景收束。"
                ),
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
        assert result.action_resolutions[0].outcome_band == "critical_success"
        assert "异常" in result.action_resolutions[0].resolved_effect

    def test_rejects_changed_outcome_band(self):
        mock = MockLLM()
        mock.set_responses([_response("advance", "failure")])

        with pytest.raises(ValueError):
            TurnResolutionAgent(mock.build_gateway()).resolve(
                user_input="观察",
                current_state=_state(),
                action_parse_result=_parse(),
                roll_results=_roll_batch(_roll(20)),
                core_memory=CoreMemory(items=[]),
                retrieved_memories=CandidateRetrievalResult(),
                scene_summaries=[],
            )

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
