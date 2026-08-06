from __future__ import annotations

from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.core_memory import CoreMemory
from app.domain.opening_foundation import OpeningFoundation

from .schemas import OpeningSettingSpecs


class OpeningSettingAgent:
    """从开局基础与 CoreMemory 推导若干世界设定规格。

    输出的 ``OpeningSettingSpecs`` 与导演侧 SettingRequirement 同构，
    供 opening_setting_node 转成 WorldSettingCreator 的输入。
    """

    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        *,
        foundation: OpeningFoundation,
        core_memory: CoreMemory,
        trace_id: str | None = None,
    ) -> OpeningSettingSpecs:
        return cast(
            OpeningSettingSpecs,
            self.llm.run(
                task="opening_setting_requirements",
                input_data={
                    "opening_foundation_json": foundation.model_dump_json(indent=2),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "output_schema_json": schema_json(OpeningSettingSpecs),
                },
                output_schema=OpeningSettingSpecs,
                temperature=0.7,
                trace_id=trace_id,
            ),
        )
