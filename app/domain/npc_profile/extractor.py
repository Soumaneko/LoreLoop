from __future__ import annotations

from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.npc_profile.schemas import NPCExtractionResult


class NPCProfileExtractor:
    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def extract(
        self,
        *,
        scene_facts: str,
        trace_id: str | None = None,
    ) -> NPCExtractionResult:
        if not scene_facts.strip():
            return NPCExtractionResult()

        return cast(
            NPCExtractionResult,
            self._gateway.run(
                task="npc_profile_extract",
                input_data={
                    "scene_facts_json": scene_facts,
                    "output_schema_json": schema_json(NPCExtractionResult),
                },
                output_schema=NPCExtractionResult,
                trace_id=trace_id,
            ),
        )
