from __future__ import annotations

import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory

from .schemas import (
    SettingSeedExtractionResult,
    SettingSourceDocument,
)


class SettingSeedExtractor:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def extract(
        self,
        *,
        core_memory: CoreMemory,
        scene_facts_json: str,
        source_documents: list[SettingSourceDocument],
        trace_id: str | None = None,
    ) -> SettingSeedExtractionResult:
        if not source_documents:
            return SettingSeedExtractionResult()
        return cast(
            SettingSeedExtractionResult,
            self._llm.run(
                task="setting_seed_extract",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_facts_json": scene_facts_json,
                    "source_documents_json": json.dumps(
                        [document.model_dump(mode="json") for document in source_documents],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        SettingSeedExtractionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=SettingSeedExtractionResult,
                temperature=0.3,
                trace_id=trace_id,
            ),
        )
