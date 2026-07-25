from __future__ import annotations

import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory

from .schemas import SettingAnchorPreparationBatch, WorldSetting


class SettingAnchorPreparer:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def prepare(
        self,
        *,
        core_memory: CoreMemory,
        settings: list[WorldSetting],
        trace_id: str | None = None,
    ) -> SettingAnchorPreparationBatch:
        if not settings:
            return SettingAnchorPreparationBatch()
        return cast(
            SettingAnchorPreparationBatch,
            self._llm.run(
                task="setting_anchor_prepare",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "settings_json": json.dumps(
                        [setting.model_dump(mode="json") for setting in settings],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        SettingAnchorPreparationBatch.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=SettingAnchorPreparationBatch,
                temperature=0.5,
                trace_id=trace_id,
            ),
        )
