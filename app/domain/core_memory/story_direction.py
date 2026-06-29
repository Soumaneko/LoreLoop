import json
from typing import cast

from pydantic import BaseModel, Field

from app.core.llm import LLMGateway

from .init import InitConfig
from .schemas import CoreMemory, CoreMemoryItem


class StoryDirectionResult(BaseModel):
    content: str = Field(min_length=1, max_length=800)
    currency_unit: str | None = Field(default=None, max_length=40)

    def to_core_memory_item(self) -> CoreMemoryItem:
        return CoreMemoryItem(
            id="core_story_direction",
            category="story",
            content=self.content,
            priority=100,
        )

    def to_core_memory_items(self) -> list[CoreMemoryItem]:
        items = [self.to_core_memory_item()]
        currency = (self.currency_unit or "").strip()
        if currency:
            items.append(CoreMemoryItem(
                id="core_currency_system",
                category="world",
                content=f"本游戏默认货币单位为「{currency}」。",
                priority=100,
            ))
        return items


class StoryDirectionAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        *,
        config: InitConfig,
        base_core_memory: CoreMemory,
        trace_id: str | None = None,
    ) -> StoryDirectionResult:
        expectation = (config.story_expectation or "").strip()
        if not expectation:
            raise ValueError("story_expectation is required")

        return cast(
            StoryDirectionResult,
            self.llm.run(
                task="story_direction",
                input_data={
                    "story_expectation": expectation,
                    "init_config_json": config.model_dump_json(indent=2),
                    "base_core_memory_json": base_core_memory.model_dump_json(indent=2),
                    "output_schema_json": json.dumps(
                        StoryDirectionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=StoryDirectionResult,
                temperature=0.4,
                trace_id=trace_id,
            ),
        )
