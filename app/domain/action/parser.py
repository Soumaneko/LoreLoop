from app.core.llm import LLMGateway
from app.domain.action.schemas import (
    ActionExtractionResult,
    ActionParseResult,
    AnchorExtractionResult,
)
from app.domain.core_memory import create_default_core_memory
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_scene_summaries,
)


class ActionParser:
    def __init__(self, action_llm: LLMGateway, anchor_llm: LLMGateway | None = None):
        self.action_llm = action_llm
        self.anchor_llm = anchor_llm or action_llm

    def parse(
        self,
        user_input: str,
        current_state: CurrentState,
        *,
        core_memory: CoreMemory | None = None,
        recent_summaries: list[SceneSummary] | None = None,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        trace_id: str | None = None,
    ) -> ActionParseResult:
        core_memory = core_memory or create_default_core_memory()
        anchor_result = self.extract_anchors(
            user_input=user_input,
            current_state=current_state,
            core_memory=core_memory,
            trace_id=trace_id,
        )
        action_result = self.extract_actions(
            user_input=user_input,
            current_state=current_state,
            core_memory=core_memory,
            recent_summaries=recent_summaries or [],
            entity_profiles=entity_profiles or [],
            trace_id=trace_id,
        )

        return ActionParseResult(
            analysis=action_result.analysis,
            turn_intent=action_result.turn_intent,
            atomic_actions=action_result.atomic_actions,
            anchors=anchor_result.anchors,
            ambiguities=[
                *action_result.ambiguities,
                *anchor_result.ambiguities,
            ],
        )

    def extract_anchors(
        self,
        *,
        user_input: str,
        current_state: CurrentState,
        core_memory: CoreMemory | None = None,
        trace_id: str | None = None,
    ) -> AnchorExtractionResult:
        core_memory = core_memory or create_default_core_memory()
        return self.anchor_llm.run(
            task="anchor_extract",
            input_data={
                "user_input": user_input,
                "current_state_json": project_current_state(current_state),
                "core_memory_json": core_memory.model_dump_json(indent=2),
            },
            output_schema=AnchorExtractionResult,
            trace_id=trace_id,
        )

    def extract_actions(
        self,
        *,
        user_input: str,
        current_state: CurrentState,
        core_memory: CoreMemory | None = None,
        recent_summaries: list[SceneSummary] | None = None,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        trace_id: str | None = None,
    ) -> ActionExtractionResult:
        core_memory = core_memory or create_default_core_memory()
        return self.action_llm.run(
            task="action_extract",
            input_data={
                "user_input": user_input,
                "current_state_json": project_current_state(current_state),
                "core_memory_json": core_memory.model_dump_json(indent=2),
                "recent_summaries_json": project_scene_summaries(recent_summaries or []),
                "entity_profiles_json": project_entity_profiles(entity_profiles or []),
            },
            output_schema=ActionExtractionResult,
            trace_id=trace_id,
        )
