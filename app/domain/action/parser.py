import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionExtractionResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
    project_scene_summaries,
)


class ActionParser:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def parse(
        self,
        user_input: str,
        current_state: CurrentState,
        *,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        trace_id: str | None = None,
    ) -> ActionExtractionResult:
        return cast(
            ActionExtractionResult,
            self.llm.run(
                task="action_extract",
                input_data={
                    "user_input": user_input,
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "recent_summaries_json": project_scene_summaries(recent_summaries or []),
                    "entity_profiles_json": project_entity_profiles(entity_profiles or []),
                    "npc_profiles_json": project_npc_profiles(npc_profiles or []),
                    "output_schema_json": json.dumps(
                        ActionExtractionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=ActionExtractionResult,
                trace_id=trace_id,
            ),
        )
