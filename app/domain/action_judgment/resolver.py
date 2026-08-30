from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.roll.resolver import action_never_rolls
from app.domain.roll.schemas import RollResultBatch

from .schemas import (
    ActionJudgment,
    OutcomeBand,
    PlayerActionResolution,
    PlayerRollModifier,
    PlayerTurnResolution,
    RiskLevel,
)


def map_roll_to_band(value: int, sides: int) -> OutcomeBand:
    if sides <= 0:
        raise ValueError("dice sides must be positive")
    if value < 1 or value > sides:
        raise ValueError(f"roll value must be between 1 and {sides}")
    ratio = value / sides
    if ratio <= 0.10:
        return "critical_failure"
    if ratio <= 0.40:
        return "failure"
    if ratio <= 0.60:
        return "partial_success"
    if ratio <= 0.90:
        return "success"
    return "critical_success"


def infer_risk_level(action: AtomicAction, band: OutcomeBand) -> RiskLevel:
    if action.risk_level is not None:
        base = action.risk_level
    elif action.action_type in {
        "attack",
        "defend",
        "cast_or_use_power",
        "hide_or_stealth",
    }:
        base = "high"
    elif action.action_type in {"speak", "observe", "inspect_object", "wait"}:
        base = "low"
    else:
        base = "medium"
    if band in {"critical_failure", "failure"}:
        if base == "low":
            return "medium"
        if base == "medium":
            return "high"
    return base


def build_action_judgments(
    parse_result: ActionParseResult,
    roll_results: RollResultBatch,
) -> list[ActionJudgment]:
    roll_by_id = {roll.action_id: roll for roll in roll_results.results}
    judgments: list[ActionJudgment] = []
    for action in parse_result.atomic_actions:
        roll = roll_by_id.get(action.action_id)
        if roll is None and action.likely_needs_roll and not action_never_rolls(action):
            raise ValueError(
                f"Missing roll result for action requiring a roll: {action.action_id}"
            )
        band: OutcomeBand = (
            map_roll_to_band(roll.value, roll.dice_sides)
            if roll is not None
            else "success"
        )
        judgments.append(
            ActionJudgment(
                action_id=action.action_id,
                sequence=action.sequence,
                actor=action.actor,
                action_type=action.action_type,
                action_text=action.raw_text,
                resolution_source="roll" if roll is not None else "automatic",
                dice=roll.dice if roll is not None else None,
                roll_value=roll.value if roll is not None else None,
                raw_value=roll.raw_value if roll is not None else None,
                dice_sides=roll.dice_sides if roll is not None else None,
                outcome_band=band,
                risk_level=infer_risk_level(action, band),
            )
        )
    return judgments


def build_player_turn_resolution(
    parse_result: ActionParseResult,
    roll_results: RollResultBatch,
) -> PlayerTurnResolution:
    roll_by_id = {roll.action_id: roll for roll in roll_results.results}
    judgment_by_id = {
        judgment.action_id: judgment
        for judgment in build_action_judgments(parse_result, roll_results)
    }
    actions: list[PlayerActionResolution] = []
    for action in sorted(parse_result.atomic_actions, key=lambda item: item.sequence):
        judgment = judgment_by_id.get(action.action_id)
        if judgment is None:
            continue
        roll = roll_by_id.get(action.action_id)
        actions.append(
            PlayerActionResolution(
                action_text=action.raw_text,
                resolution_source="roll" if roll is not None else "automatic",
                outcome_band=judgment.outcome_band,
                dice=roll.dice if roll is not None else None,
                raw_values=list(roll.all_raw_values) if roll is not None else [],
                modifiers=(
                    [
                        PlayerRollModifier(
                            label=modifier.label,
                            reason=modifier.reason,
                            value=modifier.value,
                        )
                        for modifier in roll.modifiers
                    ]
                    if roll is not None
                    else []
                ),
                modifier_total=(
                    sum(modifier.value for modifier in roll.modifiers)
                    if roll is not None
                    else 0
                ),
                final_value=roll.value if roll is not None else None,
                dice_sides=roll.dice_sides if roll is not None else None,
            )
        )
    return PlayerTurnResolution(actions=actions)
