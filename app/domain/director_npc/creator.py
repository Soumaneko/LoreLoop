import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.director_npc.schemas import DirectorNPCCreateOutput, DirectorNPCDraft
from app.domain.entity.schemas import EntityProfile


class DirectorNPCCreator:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def create(
        self,
        *,
        core_memory: CoreMemory,
        existing_npcs: list[EntityProfile],
        reserved_npcs: list[dict[str, str | None]] | None = None,
        protagonist_name: str,
        turn_index: int,
        trace_id: str | None = None,
    ) -> DirectorNPCDraft:
        existing_npc_views = []
        seen_names = set()
        for npc in existing_npcs:
            if npc.entity_type != "npc" or npc.name in seen_names:
                continue
            existing_npc_views.append({"name": npc.name, "role": npc.role})
            seen_names.add(npc.name)
        for npc in reserved_npcs or []:
            name = npc.get("name")
            if not name or name in seen_names:
                continue
            existing_npc_views.append({"name": name, "role": npc.get("role")})
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
            **result.model_dump(),
            created_turn=turn_index,
            status="standby",
            dispatched_turn=None,
        )
