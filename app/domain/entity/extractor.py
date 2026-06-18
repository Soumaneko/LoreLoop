import json
from typing import cast

from app.core.llm import LLMGateway
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
        scene_text: str,
        existing_profiles: list[EntityProfile],
        trace_id: str | None = None,
    ) -> EntityExtractionResult:
        if not scene_text.strip():
            return EntityExtractionResult()

        return cast(
            EntityExtractionResult,
            self._gateway.run(
                task="entity_extract",
                input_data={
                    "scene_text": scene_text,
                    "existing_profiles_json": json.dumps(
                        [_profile_for_extraction(profile) for profile in existing_profiles],
                        ensure_ascii=False,
                        indent=2,
                    ),
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


def _profile_for_extraction(profile: EntityProfile) -> dict:
    data = profile.semantic_view().model_dump(mode="json")
    data["entity_id"] = profile.entity_id
    return data
