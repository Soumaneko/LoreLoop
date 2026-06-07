from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck


def parse_action_workflow(
    user_input: str,
    scene_context: dict,
    parser: ActionParser,
    self_check: ActionSelfCheck,
) -> ActionParseResult:
    result = parser.parse(user_input, scene_context)
    cleaned = self_check.check(result, user_input, scene_context)
    return cleaned
