from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.scene_summary.schemas import SceneSummary


def parse_action_workflow(
    user_input: str,
    current_state: CurrentState,
    parser: ActionParser,
    self_check: ActionSelfCheck,
    *,
    core_memory: CoreMemory,
    recent_summaries: list[SceneSummary] | None = None,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    npc_profiles: list[NPCProfileDirectorView] | None = None,
) -> ActionParseResult:
    result = parser.parse(
        user_input,
        current_state,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        entity_profiles=entity_profiles or [],
        npc_profiles=npc_profiles or [],
    )
    cleaned = self_check.check(result, user_input, current_state)
    return cleaned
