import json
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.confirmed_history import HistoryDocument

from .schemas import (
    ContinuityItem,
    ContinuityProjection,
    ContinuityProjectionOutput,
)


class ContinuityProjector:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def project(
        self,
        *,
        current_documents: list[HistoryDocument],
        active_items: list[ContinuityItem],
        trace_id: str | None = None,
    ) -> ContinuityProjection:
        confirmed_scene_documents = [
            document
            for document in current_documents
            if document.source_type == "scene_paragraph"
        ]
        output = cast(
            ContinuityProjectionOutput,
            self.llm.run(
                task="continuity_project",
                input_data={
                    "confirmed_turn_json": json.dumps(
                        [
                            {
                                "evidence_id": document.document_id,
                                "source_type": document.source_type,
                                "text": document.text,
                            }
                            for document in confirmed_scene_documents
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "active_continuity_json": json.dumps(
                        [
                            {
                                "id": item.id,
                                "kind": item.kind,
                                "subject": item.subject,
                                "premise": item.premise,
                                "current_state": item.current_state,
                                "entities": item.entities,
                                "location": item.location,
                                "deadline": item.deadline,
                            }
                            for item in active_items
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": schema_json(
                        ContinuityProjectionOutput
                    ),
                },
                output_schema=ContinuityProjectionOutput,
                temperature=0.1,
                trace_id=trace_id,
            ),
        )
        return ContinuityProjection(operations=output.root)
