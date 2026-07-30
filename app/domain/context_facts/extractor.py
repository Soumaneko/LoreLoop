import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.context_facts.schemas import (
    ContextExtractorInput,
    ContextFactDraftSet,
    ContextFactSet,
)
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
    project_scene_summaries,
)

from .views import project_context_history, project_context_search_plan


class ContextExtractor:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def extract(
        self,
        input_data: ContextExtractorInput,
        *,
        trace_id: str | None = None,
    ) -> ContextFactSet:
        schema = ContextFactDraftSet.model_json_schema()
        result = cast(
            ContextFactDraftSet,
            self.llm.run(
                task="context_extract",
                input_data={
                    "user_input": input_data.user_input,
                    "core_memory_json": input_data.core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(input_data.current_state),
                    "scene_summaries_json": project_scene_summaries(input_data.scene_summaries),
                    "context_search_plan_json": project_context_search_plan(
                        input_data.search_plan
                    ),
                    "confirmed_history_json": project_context_history(
                        input_data.history_evidence
                    ),
                    "entity_profiles_json": project_entity_profiles(input_data.entity_profiles),
                    "npc_profiles_json": project_npc_profiles(input_data.npc_profiles),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=ContextFactDraftSet,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
        return ContextFactSet.from_drafts(
            turn_focus=result.turn_focus,
            facts=result.facts,
            known_evidence_ids={
                hit.evidence_id for hit in input_data.history_evidence.hits
            },
        )
