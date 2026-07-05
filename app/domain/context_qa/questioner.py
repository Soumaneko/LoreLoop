import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.context_qa.schemas import (
    ContextQuestionDraftSet,
    ContextQuestionInput,
    ContextQuestionSet,
)
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
)


class ContextQuestionAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        input_data: ContextQuestionInput,
        *,
        trace_id: str | None = None,
    ) -> ContextQuestionSet:
        schema = ContextQuestionDraftSet.model_json_schema()
        result = cast(
            ContextQuestionDraftSet,
            self.llm.run(
                task="context_questions",
                input_data={
                    "user_input": input_data.user_input,
                    "current_state_json": project_current_state(input_data.current_state),
                    "core_memory_json": input_data.core_memory.model_dump_json(indent=2),
                    "entity_profiles_json": project_entity_profiles(input_data.entity_profiles),
                    "npc_profiles_json": project_npc_profiles(input_data.npc_profiles),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=ContextQuestionDraftSet,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
        return ContextQuestionSet.from_drafts(
            turn_focus=result.turn_focus,
            questions=result.questions,
        )
