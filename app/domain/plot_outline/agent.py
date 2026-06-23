import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.director_event.views import (
    plot_outline_director_event_instructions,
    project_director_event,
)
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.plot_outline.views import (
    project_retrieved_memories,
    project_turn_resolution,
)
from app.domain.views import (
    project_action_parse,
    project_current_state,
    project_entity_profiles,
    project_scene_summaries,
)
from app.workflows.context_assembler import PlotOutlineContext


class PlotOutlineAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        context: PlotOutlineContext,
        *,
        trace_id: str | None = None,
    ) -> PlotOutlineResult:
        schema = PlotOutlineResult.model_json_schema()

        result = cast(
            PlotOutlineResult,
            self.llm.run(
                task="plot_outline",
                input_data={
                    "core_memory_json": context.core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(context.current_state),
                    "user_input": context.user_input,
                    "action_parse_result_json": project_action_parse(context.action_parse_result),
                    "turn_resolution_json": project_turn_resolution(context.turn_resolution),
                    "retrieved_memories_json": project_retrieved_memories(context.retrieved_memories),
                    "scene_summaries_json": project_scene_summaries(context.scene_summaries),
                    "entity_profiles_json": project_entity_profiles(context.entity_profiles),
                    "director_event_json": project_director_event(context.director_event),
                    "director_event_instructions": plot_outline_director_event_instructions(
                        context.director_event
                    ),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=PlotOutlineResult,
                temperature=0.8,
                trace_id=trace_id,
            ),
        )
        return result
