import random

from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.roll_modifier.schemas import RollModifierBatch
from app.domain.roll.schemas import DIFFICULTY_DICE_COUNT, RollConfig, RollResult, RollResultBatch


def _never_rolls(action: AtomicAction) -> bool:
    if action.action_type == "wait":
        return True
    return action.action_type == "move" and action.risk_level == "low"


class RollResolver:
    def __init__(self, config: RollConfig | None = None):
        self.config = config or RollConfig()

    def select_roll_actions(self, parse_result: ActionParseResult) -> list[AtomicAction]:
        actions = [a for a in parse_result.atomic_actions if not _never_rolls(a)]
        rng = random.Random(self.config.seed)

        if self.config.roll_only_when_needed:
            need_actions = [a for a in actions if a.likely_needs_roll]
            false_actions = [a for a in actions if not a.likely_needs_roll]

            if len(need_actions) >= self.config.min_rolls:
                actions = need_actions
            else:
                extra_needed = self.config.min_rolls - len(need_actions)
                extra_needed = min(extra_needed, len(false_actions))
                forced = rng.sample(false_actions, extra_needed) if extra_needed > 0 else []
                actions = need_actions + forced
                actions.sort(key=lambda a: a.sequence)

        return list(actions)

    def resolve(
        self,
        parse_result: ActionParseResult,
        *,
        modifier_batch: RollModifierBatch | None = None,
        roll_actions: list[AtomicAction] | None = None,
    ) -> RollResultBatch:
        actions = roll_actions if roll_actions is not None else self.select_roll_actions(parse_result)
        actions = [a for a in actions if not _never_rolls(a)]
        rng = random.Random(self.config.seed)
        mods_by_action = {}
        if modifier_batch is not None:
            for modifier in modifier_batch.modifiers:
                mods_by_action.setdefault(modifier.action_id, []).append(modifier)

        dice_count = DIFFICULTY_DICE_COUNT[self.config.difficulty]
        sides = self.config.dice_sides

        if dice_count == 1:
            per_action_dice = f"1d{sides}"
        else:
            per_action_dice = f"{dice_count}d{sides}kh"

        results: list[RollResult] = []

        for action in actions:
            all_raw = [rng.randint(1, sides) for _ in range(dice_count)]
            raw_value = max(all_raw)
            modifiers = mods_by_action.get(action.action_id, [])
            total_modifier = sum(modifier.value for modifier in modifiers)
            value = max(1, min(sides, raw_value + total_modifier))

            results.append(
                RollResult(
                    action_id=action.action_id,
                    sequence=action.sequence,
                    actor=action.actor,
                    action_type=action.action_type,
                    dice=per_action_dice,
                    value=value,
                    dice_sides=sides,
                    roll_hint=action.roll_hint,
                    raw_value=raw_value,
                    all_raw_values=all_raw,
                    modifiers=modifiers,
                )
            )

        if dice_count == 1:
            formula = f"{len(results)}d{sides}"
        else:
            formula = f"{len(results)}×{dice_count}d{sides}kh"

        return RollResultBatch(
            dice_formula=formula,
            dice_sides=sides,
            total_rolls=len(results),
            results=results,
        )
