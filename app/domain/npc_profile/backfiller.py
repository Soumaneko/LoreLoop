import json
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.core_memory.schemas import CoreMemory
from app.domain.creative_brief import CreativeBrief
from app.domain.npc_profile.schemas import (
    KnownAbility,
    NPCProfile,
    NPCProfileBackfillOutput,
)


class NPCProfileBackfiller:
    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def backfill(
        self,
        *,
        profile: NPCProfile,
        existing_profiles: list[NPCProfile],
        core_memory: CoreMemory,
        trace_id: str | None = None,
        guidance: str | None = None,
        creative_brief: CreativeBrief | str | None = None,
    ) -> NPCProfile:
        if isinstance(creative_brief, str):
            creative_brief_json = json.dumps(
                {"desire_pattern": creative_brief.strip()},
                ensure_ascii=False,
                indent=2,
            )
        elif isinstance(creative_brief, CreativeBrief):
            creative_brief_json = creative_brief.model_dump_json(
                exclude_none=True,
                indent=2,
            )
        else:
            creative_brief_json = "{}"

        result = cast(
            NPCProfileBackfillOutput,
            self._gateway.run(
                task="npc_profile_backfill",
                input_data={
                    "core_memory_json": core_memory.without_protagonist().model_dump_json(
                        indent=2
                    ),
                    "new_npc_profile_json": profile.public_view().model_dump_json(indent=2),
                    "existing_npcs_json": json.dumps(
                        [_existing_npc_view(npc) for npc in existing_profiles],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "creative_brief_json": creative_brief_json,
                    "director_guidance": guidance or "",
                    "output_schema_json": schema_json(NPCProfileBackfillOutput),
                },
                output_schema=NPCProfileBackfillOutput,
                trace_id=trace_id,
            ),
        )
        merged = profile.model_copy(deep=True)
        _apply_backfill(merged, result)
        return merged


def _existing_npc_view(npc: NPCProfile) -> dict:
    return {
        "name": npc.name,
        "role": npc.role,
        "core_motivation": npc.core_motivation,
        "hidden_secret": npc.hidden_secret,
    }


def _apply_backfill(profile: NPCProfile, backfill: NPCProfileBackfillOutput) -> None:
    profile.core_motivation = backfill.core_motivation.strip()
    profile.hidden_secret = backfill.hidden_secret.strip()
    profile.conflict_style = backfill.conflict_style.strip()
    profile.social_style = backfill.social_style.strip()
