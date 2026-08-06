from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.scene_facts.schemas import (
    SceneFactDraftSet,
    SceneFactExtractInput,
    SceneFactSet,
)
from app.domain.views import project_current_state


class SceneFactExtractor:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def extract(
        self,
        input_data: SceneFactExtractInput,
        *,
        trace_id: str | None = None,
    ) -> SceneFactSet:
        if not input_data.scene_text.strip():
            return SceneFactSet(scene_focus="空场景正文", facts=[])

        schema = schema_json(SceneFactDraftSet)
        result = cast(
            SceneFactDraftSet,
            self.llm.run(
                task="scene_fact_extract",
                input_data={
                    "core_memory_json": input_data.core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(input_data.current_state),
                    "scene_text": input_data.scene_text,
                    "output_schema_json": schema,
                },
                output_schema=SceneFactDraftSet,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
        return SceneFactSet.from_drafts(
            scene_focus=result.scene_focus,
            facts=result.facts,
        )
