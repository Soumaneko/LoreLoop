import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.npc_profile.schemas import NPCExtractionResult, NPCProfile


class NPCProfileExtractor:
    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def extract(
        self,
        *,
        core_memory: CoreMemory,
        scene_facts: str,
        existing_profiles: list[NPCProfile],
        trace_id: str | None = None,
    ) -> NPCExtractionResult:
        if not scene_facts.strip():
            return NPCExtractionResult()

        return cast(
            NPCExtractionResult,
            self._gateway.run(
                task="npc_profile_extract",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_facts_json": scene_facts,
                    "existing_npc_profiles_json": json.dumps(
                        [
                            _profile_for_extraction(index, profile)
                            for index, profile in enumerate(existing_profiles)
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ) if existing_profiles else "null",
                    "output_schema_json": json.dumps(
                        NPCExtractionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=NPCExtractionResult,
                trace_id=trace_id,
            ),
        )


def _profile_for_extraction(index: int, profile: NPCProfile) -> dict:
    return {
        "npc_index": index,
        "name": profile.name,
        "aliases": profile.aliases,
        "role": profile.role,
    }
