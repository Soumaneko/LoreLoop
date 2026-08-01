import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.director_npc.schemas import (
    DirectorNPCCreateOutput,
    DirectorNPCCreativeBrief,
    DirectorNPCDraft,
    NPCCreationContext,
)


class DirectorNPCCreator:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def create(
        self,
        *,
        core_memory: CoreMemory,
        existing_npcs: list,
        reserved_npcs: list[dict[str, str | None]] | None = None,
        creation_context: NPCCreationContext,
        creative_brief: DirectorNPCCreativeBrief,
        protagonist_name: str,
        turn_index: int,
        trace_id: str | None = None,
        guidance: str | None = None,
        recent_creations: list[dict] | None = None,
    ) -> DirectorNPCDraft:
        existing_npc_views = []
        seen_names = set()
        for npc in existing_npcs:
            if npc.name in seen_names:
                continue
            existing_npc_views.append(_existing_npc_view(npc))
            seen_names.add(npc.name)
        for npc in reserved_npcs or []:
            name = npc.get("name")
            if not name or name in seen_names:
                continue
            existing_npc_views.append(_existing_npc_view(npc))
            seen_names.add(name)

        result = cast(
            DirectorNPCCreateOutput,
            self._llm.run(
                task="director_npc_create",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "existing_npcs_json": json.dumps(
                        existing_npc_views,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "protagonist_name": protagonist_name,
                    "turn_index": turn_index,
                    "creation_context_json": creation_context.model_dump_json(
                        indent=2
                    ),
                    "creative_brief_json": creative_brief.model_dump_json(
                        exclude_none=True,
                        indent=2,
                    ),
                    "director_guidance": guidance or "",
                    "batch_recent_creations_json": json.dumps(
                        recent_creations or [],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        DirectorNPCCreateOutput.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=DirectorNPCCreateOutput,
                temperature=0.9,
                trace_id=trace_id,
            ),
        )
        return DirectorNPCDraft(
            **result.model_dump(exclude={"creation_reason"}),
            creation_anchors=list(creation_context.anchors),
            created_turn=turn_index,
            status="prepared",
            introduced_turn=None,
        )


def _existing_npc_view(npc) -> dict[str, str | None]:
    if isinstance(npc, dict):
        return {
            "name": npc.get("name"),
            "role": npc.get("role"),
            "core_motivation": npc.get("core_motivation"),
            "hidden_secret": npc.get("hidden_secret"),
        }
    return {
        "name": getattr(npc, "name", None),
        "role": getattr(npc, "role", None),
        "core_motivation": getattr(npc, "core_motivation", None),
        "hidden_secret": getattr(npc, "hidden_secret", None),
    }
