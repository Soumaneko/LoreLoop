import pytest

from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.roll.resolver import RollResolver
from app.domain.roll.schemas import RollConfig
from app.domain.roll_modifier.schemas import RollModifier, RollModifierBatch


def _action(action_id, sequence, *, action_type="observe", needs_roll=True, risk_level=None):
    return AtomicAction(
        action_id=action_id, sequence=sequence, actor=["主角"],
        action_type=action_type, raw_text="观察四周", verb="观察",
        likely_needs_roll=needs_roll, risk_level=risk_level,
    )


def test_default_roll_preserves_selected_action_mapping():
    actions = [_action("a1", 1), _action("a2", 2, needs_roll=False), _action("a3", 3)]
    result = RollResolver().resolve(ActionParseResult(atomic_actions=actions))
    assert result.dice_formula == "2d20"
    assert result.total_rolls == 2
    assert [(r.action_id, r.sequence, r.actor, r.action_type) for r in result.results] == [
        (a.action_id, a.sequence, a.actor, a.action_type) for a in (actions[0], actions[2])
    ]
    assert all(1 <= r.value <= 20 for r in result.results)


@pytest.mark.parametrize("difficulty,count,sides", [
    ("困难", 1, 20), ("普通", 2, 20), ("简单", 3, 6),
])
def test_difficulty_keeps_highest_before_applying_modifiers(difficulty, count, sides):
    parsed = ActionParseResult(atomic_actions=[_action("a1", 1), _action("a2", 2)])
    modifiers = RollModifierBatch(modifiers=[
        RollModifier(action_id="a1", label="支援", reason="盟友支援。", value=3),
    ])
    config = RollConfig(difficulty=difficulty, dice_sides=sides, seed=42)
    result = RollResolver(config).resolve(parsed, modifier_batch=modifiers)
    assert RollResolver(config).resolve(parsed, modifier_batch=modifiers) == result
    dice = f"{count}d{sides}" + ("kh" if count > 1 else "")
    assert result.dice_formula == (f"2d{sides}" if count == 1 else f"2×{dice}")
    for roll, adjustment in zip(result.results, (3, 0)):
        assert roll.dice == dice
        assert len(roll.all_raw_values) == count
        assert all(1 <= value <= sides for value in roll.all_raw_values)
        assert roll.raw_value == max(roll.all_raw_values)
        assert roll.value == max(1, min(sides, roll.raw_value + adjustment))
    assert result.results[0].modifiers == modifiers.modifiers


@pytest.mark.parametrize("seed,modifier,raw,final", [(1, 5, 19, 20), (2, -5, 2, 1)])
def test_modifier_clamps_to_dice_range(seed, modifier, raw, final):
    parsed = ActionParseResult(atomic_actions=[_action("a1", 1)])
    modifiers = RollModifierBatch(modifiers=[
        RollModifier(action_id="a1", label="环境", reason="环境影响。", value=modifier),
    ])
    selected = [_action("skip", 1), _action("a1", 2)] if seed == 1 else None
    result = RollResolver(RollConfig(seed=seed)).resolve(
        parsed, modifier_batch=modifiers, roll_actions=selected,
    )
    roll = next(r for r in result.results if r.action_id == "a1")
    assert (roll.raw_value, roll.value) == (raw, final)


def test_automatic_actions_are_excluded_even_from_explicit_roll_selection():
    actions = [
        _action("wait", 1, action_type="wait"),
        _action("walk", 2, action_type="move", risk_level="low"),
        _action("climb", 3, action_type="move", risk_level="high"),
    ]
    parsed = ActionParseResult(atomic_actions=actions)
    resolver = RollResolver()
    selected = resolver.select_roll_actions(parsed)
    assert [a.action_id for a in selected] == ["climb"]
    result = resolver.resolve(parsed, roll_actions=actions)
    assert [r.action_id for r in result.results] == ["climb"]


@pytest.mark.parametrize("actions", [[], [_action("a1", 1, needs_roll=False)]], ids=["empty", "automatic"])
def test_no_selected_actions_produce_no_dice(actions):
    result = RollResolver().resolve(ActionParseResult(atomic_actions=actions))
    assert result.total_rolls == 0
    assert result.results == []
