import random

from app.domain.action.schemas import ActionParseResult
from app.domain.roll.schemas import RollConfig, RollResult, RollResultBatch


class RollResolver:
    def __init__(self, config: RollConfig | None = None):
        self.config = config or RollConfig()

    def resolve(self, parse_result: ActionParseResult) -> RollResultBatch:
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

        results: list[RollResult] = []

        for action in actions:
            value = rng.randint(1, self.config.dice_sides)

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
                )
            )

        return RollResultBatch(
            dice_formula=f"{len(results)}d{self.config.dice_sides}",
            dice_sides=self.config.dice_sides,
            total_rolls=len(results),
            results=results,
        )
