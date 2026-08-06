from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json

from .schemas import CoreMemoryItem

if TYPE_CHECKING:
    from .init import InitConfig


PowerPrevalence = Literal["罕见", "少数人掌握", "较为常见", "社会基础"]
PowerLearningDifficulty = Literal["困难", "适中", "轻松"]
WorldSubject = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=10,
        pattern=r"^[^\r\n]+$",
    ),
]
WorldConflict = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=50,
        pattern=r"^[^\r\n]+$",
    ),
]
DominantPower = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=10,
        pattern=r"^[^\r\n]+$",
    ),
]


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

    version: Literal[2] = 2
    currency_unit: str = Field(min_length=1, max_length=40)
    public_power_baseline: PublicPowerBaseline
    peoples_and_beings: list[WorldSubject] = Field(
        min_length=1,
        max_length=6,
    )
    major_world_conflicts: list[WorldConflict] = Field(
        min_length=1,
        max_length=2,
    )
    dominant_powers: list[DominantPower] = Field(
        min_length=1,
        max_length=3,
    )
    civilization_baseline: str = Field(min_length=1, max_length=200)

    @field_validator("currency_unit")
    @classmethod
    def normalize_currency_unit(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean

    @field_validator("civilization_baseline")
    @classmethod
    def normalize_civilization_baseline(cls, value: str) -> str:
        clean = value.strip()
        if "\n" in clean or "\r" in clean:
            raise ValueError("must be one line")
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
            CoreMemoryItem(
                id="core_peoples_and_beings",
                kind="world_invariant",
                strength="hard_constraint",
                source="campaign_setup",
                content="世界中的主要存在主体：" + "、".join(self.peoples_and_beings),
            ),
            CoreMemoryItem(
                id="core_major_world_conflicts",
                kind="world_invariant",
                strength="hard_constraint",
                source="campaign_setup",
                content="长期塑造世界的主要冲突：\n- "
                + "\n- ".join(self.major_world_conflicts),
            ),
            CoreMemoryItem(
                id="core_dominant_powers",
                kind="world_invariant",
                strength="hard_constraint",
                source="campaign_setup",
                content="能够影响普通人命运的主导势力："
                + "、".join(self.dominant_powers),
            ),
            CoreMemoryItem(
                id="core_civilization_baseline",
                kind="world_invariant",
                strength="hard_constraint",
                source="campaign_setup",
                content=f"文明与技术基准：{self.civilization_baseline}",
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
                    "output_schema_json": schema_json(
                        CampaignSetupProposal
                    ),
                },
                output_schema=CampaignSetupProposal,
                temperature=0.4,
                trace_id=trace_id,
            ),
        )
