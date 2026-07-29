from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.llm import LLMGateway

from .schemas import CoreMemoryItem

if TYPE_CHECKING:
    from .init import InitConfig


PowerPrevalence = Literal["罕见", "少数人掌握", "较为常见", "社会基础"]
PowerLearningDifficulty = Literal["困难", "适中", "轻松"]


class PublicPowerBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=120)
    prevalence: PowerPrevalence
    learning_difficulty: PowerLearningDifficulty
    limitation: str = Field(min_length=1, max_length=80)

    @field_validator("source", "limitation")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        if "\n" in clean or "\r" in clean:
            raise ValueError("must be one line")
        return clean

    @field_validator("limitation")
    @classmethod
    def require_one_sentence(cls, value: str) -> str:
        sentence_endings = sum(value.count(mark) for mark in "。！？!?")
        if sentence_endings > 1:
            raise ValueError("must contain exactly one sentence")
        if "；" in value or ";" in value:
            raise ValueError("must not enumerate clauses with semicolons")
        return value


class CampaignSetupProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    currency_unit: str = Field(min_length=1, max_length=40)
    public_power_baseline: PublicPowerBaseline

    @field_validator("currency_unit")
    @classmethod
    def normalize_currency_unit(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean

    def to_core_memory_items(self) -> list[CoreMemoryItem]:
        baseline = self.public_power_baseline
        return [
            CoreMemoryItem(
                id="core_currency_system",
                kind="system_convention",
                strength="hard_constraint",
                source="campaign_setup",
                content=f"默认货币单位：{self.currency_unit}",
            ),
            CoreMemoryItem(
                id="core_public_power_baseline",
                kind="ability_rule",
                strength="hard_constraint",
                source="campaign_setup",
                content=(
                    f"公开力量来源：{baseline.source}\n"
                    f"社会普及率：{baseline.prevalence}\n"
                    f"学习难度：{baseline.learning_difficulty}\n"
                    f"公开限制：{baseline.limitation}"
                ),
            ),
        ]


class CampaignSetupAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        *,
        config: "InitConfig",
        trace_id: str | None = None,
    ) -> CampaignSetupProposal:
        return cast(
            CampaignSetupProposal,
            self.llm.run(
                task="campaign_setup",
                input_data={
                    "init_config_json": config.model_dump_json(indent=2),
                    "output_schema_json": json.dumps(
                        CampaignSetupProposal.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=CampaignSetupProposal,
                temperature=0.4,
                trace_id=trace_id,
            ),
        )
