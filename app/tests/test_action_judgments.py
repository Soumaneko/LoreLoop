import pytest

from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.roll.schemas import RollResult, RollResultBatch
from app.domain.action_judgment import build_action_judgments, map_roll_to_band


def test_roll_band_boundaries():
    assert map_roll_to_band(2, 20) == "critical_failure"
    assert map_roll_to_band(8, 20) == "failure"
    assert map_roll_to_band(12, 20) == "partial_success"
    assert map_roll_to_band(18, 20) == "success"
    assert map_roll_to_band(19, 20) == "critical_success"


def test_action_judgments_combine_program_rolls_and_automatic_successes():
    actions = [
        AtomicAction(
            action_id="A1",
            sequence=1,
            actor=["玩家"],
            action_type="attack",
            raw_text="攻击",
            verb="攻击",
            likely_needs_roll=True,
        ),
        AtomicAction(
            action_id="A2",
            sequence=2,
            actor=["玩家"],
            action_type="wait",
            raw_text="等待",
            verb="等待",
            likely_needs_roll=False,
        ),
    ]
    rolls = RollResultBatch(
        dice_formula="1d20",
        dice_sides=20,
        total_rolls=1,
        results=[RollResult(
            action_id="A1",
            sequence=1,
            actor=["玩家"],
            action_type="attack",
            dice="1d20",
            value=5,
            dice_sides=20,
        )],
    )
    judgments = build_action_judgments(
        ActionParseResult(atomic_actions=actions), rolls
    )
    assert [item.outcome_band for item in judgments] == ["failure", "success"]
    assert [item.resolution_source for item in judgments] == ["roll", "automatic"]


def test_missing_required_roll_fails_closed():
    action = AtomicAction(
        action_id="A1",
        sequence=1,
        actor=["玩家"],
        action_type="attack",
        raw_text="攻击",
        verb="攻击",
        likely_needs_roll=True,
    )
    empty = RollResultBatch(
        dice_formula="0d20", dice_sides=20, total_rolls=0, results=[]
    )
    with pytest.raises(ValueError, match="Missing roll result"):
        build_action_judgments(ActionParseResult(atomic_actions=[action]), empty)
