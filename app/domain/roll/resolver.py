import random

from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.roll_modifier.schemas import RollModifierBatch
from app.domain.roll.schemas import RollConfig, RollResult, RollResultBatch


class RollResolver:
    def __init__(self, config: RollConfig | None = None):
        self.config = config or RollConfig()

    def select_roll_actions(self, parse_result: ActionParseResult) -> list[AtomicAction]:
        actions = parse_result.atomic_actions
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
        rng = random.Random(self.config.seed)
        mods_by_action = {}
        if modifier_batch is not None:
            for modifier in modifier_batch.modifiers:
                mods_by_action.setdefault(modifier.action_id, []).append(modifier)

        results: list[RollResult] = []

        for action in actions:
            raw_value = rng.randint(1, self.config.dice_sides)
            modifiers = mods_by_action.get(action.action_id, [])
            total_modifier = sum(modifier.value for modifier in modifiers)
            value = max(1, min(self.config.dice_sides, raw_value + total_modifier))

            results.append(
                RollResult(
                    action_id=action.action_id,
                    sequence=action.sequence,
                    actor=action.actor,
                    action_type=action.action_type,
                    dice=f"1d{self.config.dice_sides}",
                    value=value,
                    dice_sides=self.config.dice_sides,
                    roll_hint=action.roll_hint,
                    raw_value=raw_value,
                    modifiers=modifiers,
                )
            )

        return RollResultBatch(
            dice_formula=f"{len(results)}d{self.config.dice_sides}",
            dice_sides=self.config.dice_sides,
            total_rolls=len(results),
            results=results,
        )
