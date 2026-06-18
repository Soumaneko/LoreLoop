import json

import pytest

from app.core.llm import MockLLM
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, ResourcesState, SceneState
from app.domain.narrative_pacer import (
    NarrativePacer,
    cooldown_filter_types,
    roll_filter_types,
)
from app.domain.outcome import OutcomeFrame, OutcomeResolutionResult
from app.domain.scene_summary.schemas import SceneSummary
from app.workflows.pace_narrative import pace_narrative_workflow


def _outcome(action_id: str, band: str) -> OutcomeFrame:
    return OutcomeFrame(
        action_id=action_id,
        sequence=1,
        actor="主角",
        action_type="observe",
        dice="1d20",
        roll_value=1,
        dice_sides=20,
        outcome_band=band,
        required_consequence="test consequence",
        information_policy="none" if "failure" in band else "clear_answer_allowed",
    )


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="北郡"),
        player=PlayerState(name="卢西安"),
        resources=ResourcesState(),
    )


def _recent_summaries() -> list[SceneSummary]:
    return []


def _parse_result() -> ActionParseResult:
    return ActionParseResult(
        turn_intent="观察",
        atomic_actions=[
            AtomicAction(
                action_id="a1",
                sequence=1,
                actor="主角",
                action_type="observe",
                raw_text="观察四周",
                verb="观察",
                likely_needs_roll=True,
                confidence=0.9,
            )
        ],
        anchors=[],
        ambiguities=[],
    )


def _pace_response(selected_type: str = "advance") -> str:
    return json.dumps(
        {
            "selected_type": selected_type,
            "narrative_goal": "回应当前行动，并推动当前目标向前。",
        },
        ensure_ascii=False,
    )


class TestNarrativePacerFilters:
    def test_critical_success_and_failure_keeps_all_subtypes(self):
        result = OutcomeResolutionResult(
            outcomes=[_outcome("a1", "critical_success"), _outcome("a2", "critical_failure")],
            total_outcomes=2,
        )

        available, notes = roll_filter_types(result)

        assert "reveal_information" in available
        assert "complication" in available
        assert any("both present" in note for note in notes)

    def test_critical_failure_only_allows_pressure_or_complication(self):
        result = OutcomeResolutionResult(
            outcomes=[_outcome("a1", "critical_failure")],
            total_outcomes=1,
        )

        available, _ = roll_filter_types(result)

        assert available == ["escalate_pressure", "complication"]

    def test_no_failure_like_removes_pressure_and_complication(self):
        result = OutcomeResolutionResult(
            outcomes=[_outcome("a1", "success")],
            total_outcomes=1,
        )

        available, _ = roll_filter_types(result)

        assert "escalate_pressure" not in available
        assert "complication" not in available

    def test_no_success_like_removes_information_and_transition(self):
        result = OutcomeResolutionResult(
            outcomes=[_outcome("a1", "failure")],
            total_outcomes=1,
        )

        available, _ = roll_filter_types(result)

        assert "reveal_information" not in available
        assert "transition" not in available

    def test_cannot_repeat_previous_subtype_except_advance(self):
        available, _ = cooldown_filter_types(
            ["advance", "relationship_shift", "complication"],
            ["relationship_shift"],
        )

        assert "relationship_shift" not in available
        assert "advance" in available

    def test_two_advances_cool_down_advance(self):
        available, _ = cooldown_filter_types(
            ["advance", "relationship_shift"],
            ["advance", "advance"],
        )

        assert "advance" not in available
        assert available == ["relationship_shift"]

    def test_transition_and_respite_share_cooldown(self):
        available, _ = cooldown_filter_types(
            ["advance", "transition", "respite"],
            ["transition"],
        )

        assert available == ["advance"]


class TestNarrativePacerLLM:
    def test_pacer_selects_available_subtype(self):
        mock = MockLLM()
        mock.set_responses([_pace_response("advance")])

        result = NarrativePacer(mock.build_gateway()).pace(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse_result(),
            outcome_resolution=OutcomeResolutionResult(
                outcomes=[_outcome("a1", "success")],
                total_outcomes=1,
            ),
            core_memory=CoreMemory(items=[]),
            recent_summaries=_recent_summaries(),
        )

        assert result.selected_type == "advance"
        assert result.narrative_goal == "回应当前行动，并推动当前目标向前。"
        assert "complication" not in result.available_types

    def test_pacer_rejects_unavailable_llm_choice(self):
        mock = MockLLM()
        mock.set_responses([_pace_response("complication")])

        with pytest.raises(ValueError):
            NarrativePacer(mock.build_gateway()).pace(
                user_input="观察",
                current_state=_state(),
                action_parse_result=_parse_result(),
                outcome_resolution=OutcomeResolutionResult(
                    outcomes=[_outcome("a1", "success")],
                    total_outcomes=1,
                ),
                core_memory=CoreMemory(items=[]),
                recent_summaries=_recent_summaries(),
            )

    def test_workflow_returns_valid_result(self):
        mock = MockLLM()
        mock.set_responses([_pace_response("advance")])

        result = pace_narrative_workflow(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse_result(),
            outcome_resolution=OutcomeResolutionResult(
                outcomes=[_outcome("a1", "success")],
                total_outcomes=1,
            ),
            core_memory=CoreMemory(items=[]),
            recent_summaries=_recent_summaries(),
            gateway=mock.build_gateway(),
        )

        assert result.selected_type == "advance"
