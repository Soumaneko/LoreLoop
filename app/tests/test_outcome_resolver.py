from copy import deepcopy

from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.current_state import CurrentState, PlayerState, ResourcesState, SceneState
from app.domain.outcome import OutcomeResolver, build_information_policy, map_roll_to_band
from app.domain.roll.schemas import RollResult, RollResultBatch
from app.workflows.resolve_outcomes import resolve_outcomes_workflow


def _make_action(
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


def _make_parse_result(action: AtomicAction | None = None) -> ActionParseResult:
    return ActionParseResult(
        turn_intent="观察",
        atomic_actions=[action or _make_action()],
        anchors=[],
        ambiguities=[],
    )


def _make_roll(value: int = 10, action_id: str = "a1") -> RollResult:
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


def _make_roll_batch(*rolls: RollResult) -> RollResultBatch:
    roll_list = list(rolls)
    return RollResultBatch(
        dice_formula=f"{len(roll_list)}d20",
        dice_sides=20,
        total_rolls=len(roll_list),
        results=roll_list,
    )


def _make_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="北郡", time_label="清晨"),
        player=PlayerState(name="卢西安"),
        resources=ResourcesState(),
    )


class TestOutcomeBandMapping:
    def test_roll_1_on_d20_is_critical_failure(self):
        assert map_roll_to_band(1, 20) == "critical_failure"

    def test_roll_20_on_d20_is_critical_success(self):
        assert map_roll_to_band(20, 20) == "critical_success"

    def test_low_roll_cannot_produce_success_band(self):
        assert map_roll_to_band(7, 20) in {"critical_failure", "failure"}

    def test_high_roll_cannot_produce_failure_band(self):
        assert map_roll_to_band(17, 20) in {"success", "critical_success"}

    def test_information_policy_follows_outcome_band(self):
        assert build_information_policy("critical_failure") == "none"
        assert build_information_policy("failure") == "ambiguous_hint_only"
        assert build_information_policy("partial_success") == "partial_disclosure_with_cost"
        assert build_information_policy("success") == "clear_answer_allowed"
        assert build_information_policy("critical_success") == "clear_answer_with_advantage"


class TestOutcomeResolver:
    def test_resolves_roll_to_outcome_frame(self):
        result = OutcomeResolver().resolve(
            parse_result=_make_parse_result(),
            roll_results=_make_roll_batch(_make_roll(13)),
            current_state=_make_state(),
        )

        assert result.total_outcomes == 1
        frame = result.outcomes[0]
        assert frame.action_id == "a1"
        assert frame.roll_value == 13
        assert frame.outcome_band == "partial_success"
        assert frame.information_policy == "partial_disclosure_with_cost"
        assert "cost" in frame.required_consequence

    def test_unknown_action_id_is_ignored(self):
        result = OutcomeResolver().resolve(
            parse_result=_make_parse_result(),
            roll_results=_make_roll_batch(_make_roll(20, action_id="missing")),
            current_state=_make_state(),
        )

        assert result.outcomes == []
        assert result.total_outcomes == 0

    def test_empty_roll_batch_returns_empty_result(self):
        result = OutcomeResolver().resolve(
            parse_result=_make_parse_result(),
            roll_results=_make_roll_batch(),
            current_state=_make_state(),
        )

        assert result.outcomes == []
        assert result.total_outcomes == 0

    def test_does_not_mutate_roll_result_batch(self):
        roll_results = _make_roll_batch(_make_roll(1))
        before = deepcopy(roll_results)

        OutcomeResolver().resolve(
            parse_result=_make_parse_result(),
            roll_results=roll_results,
            current_state=_make_state(),
        )

        assert roll_results == before

    def test_infers_high_risk_for_combat_or_magic_actions(self):
        action = _make_action(action_type="cast_or_use_power")
        roll = _make_roll(20)
        roll.action_type = "cast_or_use_power"

        result = OutcomeResolver().resolve(
            parse_result=_make_parse_result(action),
            roll_results=_make_roll_batch(roll),
            current_state=_make_state(),
        )

        assert result.outcomes[0].risk_level == "high"

    def test_uses_llm_risk_level_before_type_fallback(self):
        action = _make_action(action_type="observe", risk_level="high")

        result = OutcomeResolver().resolve(
            parse_result=_make_parse_result(action),
            roll_results=_make_roll_batch(_make_roll(20)),
            current_state=_make_state(),
        )

        assert result.outcomes[0].risk_level == "high"

    def test_failure_band_raises_llm_low_risk_to_medium(self):
        action = _make_action(action_type="observe", risk_level="low")

        result = OutcomeResolver().resolve(
            parse_result=_make_parse_result(action),
            roll_results=_make_roll_batch(_make_roll(5)),
            current_state=_make_state(),
        )

        assert result.outcomes[0].outcome_band == "failure"
        assert result.outcomes[0].risk_level == "medium"

    def test_workflow_returns_valid_outcome_resolution(self):
        result = resolve_outcomes_workflow(
            parse_result=_make_parse_result(),
            roll_results=_make_roll_batch(_make_roll(20)),
            current_state=_make_state(),
        )

        assert result.total_outcomes == 1
        assert result.outcomes[0].outcome_band == "critical_success"
