import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.director_npc.schemas import (
    DirectorNPCCreateOutput,
    DirectorNPCCreativeBrief,
    DirectorNPCDraft,
)
from app.domain.world_setting.schemas import SettingAnchorPacket


class DirectorNPCCreator:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def create(
        self,
        *,
        core_memory: CoreMemory,
        existing_npcs: list,
        reserved_npcs: list[dict[str, str | None]] | None = None,
        setting_anchor: SettingAnchorPacket,
        creative_brief: DirectorNPCCreativeBrief,
        protagonist_name: str,
        turn_index: int,
        trace_id: str | None = None,
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
                    "setting_anchor_json": json.dumps(
                        _setting_anchor_view(setting_anchor),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "creative_brief_json": creative_brief.model_dump_json(indent=2),
                    "output_schema_json": json.dumps(
                        DirectorNPCCreateOutput.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=DirectorNPCCreateOutput,
                temperature=0.9,
                trace_id=trace_id,
                output_validator=lambda output: _validate_label_used_in_business_fields(
                    cast(DirectorNPCCreateOutput, output),
                    setting_anchor.label,
                ),
            ),
        )
        return DirectorNPCDraft(
            **result.model_dump(exclude={"setting_expansion_note"}),
            source_setting_id=setting_anchor.setting_id,
            creative_brief=creative_brief,
            created_turn=turn_index,
            status="standby",
            dispatched_turn=None,
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


def _setting_anchor_view(anchor: SettingAnchorPacket) -> dict:
    return {
        "label": anchor.label,
        "kind": anchor.kind,
        "definition": anchor.definition,
        "setting_facts": list(anchor.setting_facts),
        "world_effects": list(anchor.world_effects),
    }


def _validate_label_used_in_business_fields(
    output: DirectorNPCCreateOutput,
    label: str,
) -> None:
    clean_label = str(label).strip()
    if not clean_label:
        return
    business_payload = output.model_dump(exclude={"setting_expansion_note"})
    if clean_label in json.dumps(business_payload, ensure_ascii=False):
        return
    raise ValueError(
        "DirectorNPC label anchoring failed: persisted business fields did not "
        f"include the exact setting label {clean_label!r}. This is lazy "
        "anchor substitution: adjacent concepts, broader categories, partial "
        "terms, related organizations, places, pressures, or risks cannot "
        "replace the exact label. Retry by rewriting persisted NPC fields so "
        "the complete label appears naturally at least once."
    )
