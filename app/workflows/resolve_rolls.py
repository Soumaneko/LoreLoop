from app.domain.action.schemas import ActionParseResult
from app.domain.roll.resolver import RollResolver
from app.domain.roll.schemas import RollConfig, RollResultBatch


def resolve_rolls_workflow(
    parse_result: ActionParseResult,
    *,
    config: RollConfig | None = None,
) -> RollResultBatch:
    resolver = RollResolver(config)
    return resolver.resolve(parse_result)
