from __future__ import annotations

import json
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.core_memory.schemas import CoreMemory

from .schemas import SettingCreateOutput, SettingKind, WorldSetting


class WorldSettingCreator:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def create(
        self,
        *,
        kind: SettingKind,
        guidance: str,
        core_memory: CoreMemory,
        existing_settings: list[WorldSetting],
        turn_index: int,
        trace_id: str | None = None,
    ) -> WorldSetting:
        output = cast(
            SettingCreateOutput,
            self._llm.run(
                task="director_setting_create",
                input_data={
                    "kind": kind,
                    "guidance": guidance,
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "existing_settings_json": json.dumps(
                        [setting.dedup_view() for setting in existing_settings],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": schema_json(SettingCreateOutput),
                },
                output_schema=SettingCreateOutput,
                temperature=0.9,
                trace_id=trace_id,
            ),
        )
        return WorldSetting(
            kind=kind,
            **output.model_dump(),
            related_setting_ids=[],
            created_turn=turn_index,
        )
