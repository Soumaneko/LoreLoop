import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory import CoreMemory

from .schemas import OpeningFoundation


class OpeningFoundationAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        *,
        core_memory: CoreMemory,
        trace_id: str | None = None,
    ) -> OpeningFoundation:
        return cast(
            OpeningFoundation,
            self.llm.run(
                task="opening_foundation",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "output_schema_json": json.dumps(
                        OpeningFoundation.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=OpeningFoundation,
                temperature=0.7,
                trace_id=trace_id,
            ),
        )
