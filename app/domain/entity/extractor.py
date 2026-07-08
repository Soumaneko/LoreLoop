import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.entity.schemas import (
    EntityExtractionResult,
    EntityProfile,
)


class EntityExtractor:
    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def extract(
        self,
        *,
        core_memory: CoreMemory,
        scene_facts: str,
        existing_profiles: list[EntityProfile],
        trace_id: str | None = None,
    ) -> EntityExtractionResult:
        if not scene_facts.strip():
            return EntityExtractionResult()

        return cast(
            EntityExtractionResult,
            self._gateway.run(
                task="entity_extract",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_facts_json": scene_facts,
                    "existing_profiles_json": json.dumps(
                        [
                            _profile_for_extraction(index, profile)
                            for index, profile in enumerate(existing_profiles)
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ) if existing_profiles else "null",
                    "output_schema_json": json.dumps(
                        EntityExtractionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=EntityExtractionResult,
                trace_id=trace_id,
            ),
        )


def _profile_for_extraction(index: int, profile: EntityProfile) -> dict:
    return {
        "entity_index": index,
        "entity_type": profile.entity_type,
        "name": profile.name,
        "aliases": profile.aliases,
    }
