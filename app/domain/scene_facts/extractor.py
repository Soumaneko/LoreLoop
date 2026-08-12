import json
from typing import cast

from pydantic import BaseModel

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

        allowed_paragraph_ids = {
            paragraph.document_id for paragraph in input_data.scene_paragraphs
        }

        def validate_paragraph_evidence(output: BaseModel) -> None:
            draft = cast(SceneFactDraftSet, output)
            referenced: set[str] = set()
            for mention in draft.npc_mentions:
                referenced.update(mention.evidence_paragraph_ids)
                referenced.update(
                    paragraph_id
                    for relation in mention.alias_relations
                    for paragraph_id in relation.evidence_paragraph_ids
                )
            invalid = sorted(referenced - allowed_paragraph_ids)
            if invalid:
                raise ValueError(
                    "npc_mentions reference unknown paragraph ids "
                    f"{invalid}; ids must come from the input scene_paragraphs"
                )

        schema = schema_json(SceneFactDraftSet)
        result = cast(
            SceneFactDraftSet,
            self.llm.run(
                task="scene_fact_extract",
                input_data={
                    "core_memory_json": input_data.core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(input_data.current_state),
                    "scene_text": input_data.scene_text,
                    "scene_paragraphs_json": _scene_paragraphs_json(
                        input_data.scene_paragraphs
                    ),
                    "output_schema_json": schema,
                },
                output_schema=SceneFactDraftSet,
                output_validator=validate_paragraph_evidence,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
        return SceneFactSet.from_drafts(
            scene_focus=result.scene_focus,
            facts=result.facts,
            npc_mentions=result.npc_mentions,
        )


def _scene_paragraphs_json(paragraphs) -> str:
    return json.dumps(
        [
            {"document_id": paragraph.document_id, "text": paragraph.text}
            for paragraph in paragraphs
        ],
        ensure_ascii=False,
        indent=2,
    )
