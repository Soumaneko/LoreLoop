import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.plot_outline.schemas import PlotOutlineResult
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
                    "current_state_json": context.current_state.model_dump_json(indent=2),
                    "user_input": context.user_input,
                    "action_parse_result_json": context.action_parse_result.model_dump_json(indent=2),
                    "roll_results_json": context.roll_results.model_dump_json(indent=2),
                    "outcome_resolution_json": context.outcome_resolution.model_dump_json(indent=2),
                    "narrative_pacing_json": context.narrative_pacing.model_dump_json(indent=2),
                    "short_term_memory_json": context.short_term_memory.model_dump_json(indent=2),
                    "retrieved_memories_json": context.retrieved_memories.model_dump_json(indent=2),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=PlotOutlineResult,
                trace_id=trace_id,
            ),
        )
        return result
