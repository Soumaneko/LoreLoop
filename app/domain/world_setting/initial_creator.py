from __future__ import annotations

import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.core_memory.story_direction import StoryDirectionResult

from .schemas import InitialWorldSettingCreationResult


class InitialWorldSettingCreator:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def create(
        self,
        *,
        core_memory: CoreMemory,
        story_direction: StoryDirectionResult,
        trace_id: str | None = None,
    ) -> InitialWorldSettingCreationResult:
        return cast(
            InitialWorldSettingCreationResult,
            self._llm.run(
                task="init_world_setting_create",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "story_direction": story_direction.content,
                    "output_schema_json": json.dumps(
                        InitialWorldSettingCreationResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=InitialWorldSettingCreationResult,
                temperature=0.8,
                trace_id=trace_id,
            ),
        )
