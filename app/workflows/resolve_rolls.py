from app.domain.action.schemas import ActionParseResult
from app.domain.action.schemas import AtomicAction
from app.domain.roll.resolver import RollResolver
from app.domain.roll.schemas import RollConfig, RollResultBatch
from app.domain.roll_modifier.schemas import RollModifierBatch


def resolve_rolls_workflow(
    parse_result: ActionParseResult,
    *,
    config: RollConfig | None = None,
    modifier_batch: RollModifierBatch | None = None,
    roll_actions: list[AtomicAction] | None = None,
) -> RollResultBatch:
    resolver = RollResolver(config)
    return resolver.resolve(
        parse_result,
        modifier_batch=modifier_batch,
        roll_actions=roll_actions,
    )
