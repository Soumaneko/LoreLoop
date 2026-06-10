from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.current_state.schemas import CurrentState


def parse_action_workflow(
    user_input: str,
    current_state: CurrentState,
    parser: ActionParser,
    self_check: ActionSelfCheck,
) -> ActionParseResult:
    result = parser.parse(user_input, current_state)
    cleaned = self_check.check(result, user_input, current_state)
    return cleaned
