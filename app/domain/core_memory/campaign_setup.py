from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.core.llm import LLMGateway
from app.core.llm.schema import display_schema_json

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


class CampaignSetupProposalWire(BaseModel):
    """宽松 wire：soft_text 字段放宽至 S，其余约束原样保留。

    strict_name（currency_unit、peoples/powers 名称）保持产品上限即 Accept
    上限，由 pydantic 严格拒绝；soft_text 的 A=S=现有产品上限，仅
    maxLength 放宽，由 output_validator 在 H/A/S 间分流。
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    currency_unit: str = Field(min_length=1, max_length=40)
    public_power_baseline: PublicPowerBaselineWire
    peoples_and_beings: list[WorldSubject] = Field(
        min_length=1,
        max_length=6,
    )
    major_world_conflicts: list[str] = Field(
        min_length=1,
        max_length=2,
    )
    dominant_powers: list[DominantPower] = Field(
        min_length=1,
        max_length=3,
    )
    civilization_baseline: str = Field(min_length=1)

    # soft_text 字段（冲突/文明基线）在 wire 上不设上限，把 A/S 判定交给
    # output_validator，确保二轮超过 S 时能进入截断分支（而非先被 Schema 拒绝）。
    @field_validator("major_world_conflicts")
    @classmethod
    def _conflicts_are_single_line(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            clean = value.strip()
            if not clean:
                raise ValueError("must not be blank")
            if "\n" in clean or "\r" in clean:
                raise ValueError("must be one line")
            cleaned.append(clean)
        return cleaned

    @field_validator("civilization_baseline")
    @classmethod
    def normalize_civilization_baseline(cls, value: str) -> str:
        clean = value.strip()
        if "\n" in clean or "\r" in clean:
            raise ValueError("must be one line")
        return clean


class PublicPowerBaselineWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    prevalence: PowerPrevalence
    learning_difficulty: PowerLearningDifficulty
    limitation: str = Field(min_length=1)

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


#: soft_text 字段的 Prompt 目标 H；A=S=现有产品上限。
CAMPAIGN_SETUP_PROMPT_TARGETS = {
    ("public_power_baseline", "source"): 100,
    ("public_power_baseline", "limitation"): 60,
    "major_world_conflicts": 40,
    "civilization_baseline": 160,
}


def _clamp_campaign_setup(value: CampaignSetupProposalWire) -> dict[str, Any]:
    """把 wire 输出规范化为领域模型可接受的 dict（超 A 的 soft_text 截断）。"""

    baseline = value.public_power_baseline
    return {
        "version": value.version,
        "currency_unit": value.currency_unit,
        "public_power_baseline": {
            "source": baseline.source[:120],
            "prevalence": baseline.prevalence,
            "learning_difficulty": baseline.learning_difficulty,
            "limitation": baseline.limitation[:80],
        },
        "peoples_and_beings": value.peoples_and_beings,
        "major_world_conflicts": [
            conflict[:50] for conflict in value.major_world_conflicts
        ],
        "dominant_powers": value.dominant_powers,
        "civilization_baseline": value.civilization_baseline[:200],
    }


class CampaignSetupAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[str] = []

    def generate(
        self,
        *,
        config: "InitConfig",
        trace_id: str | None = None,
    ) -> CampaignSetupProposal:
        current_attempt = 0
        pending: dict[str, Any] | None = None
        pending_warnings: list[str] = []
        self.last_warnings = []

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: CampaignSetupProposalWire) -> None:
            nonlocal pending, pending_warnings
            warnings: list[str] = []
            if current_attempt == 1:
                over = _campaign_setup_accept_errors(output)
                if over:
                    raise ValueError("; ".join(over))
            else:
                baseline = output.public_power_baseline
                if len(baseline.source) > 120:
                    warnings.append("public_power_baseline.source truncated")
                if len(baseline.limitation) > 80:
                    warnings.append(
                        "public_power_baseline.limitation truncated"
                    )
                if any(
                    len(conflict) > 50
                    for conflict in output.major_world_conflicts
                ):
                    warnings.append("major_world_conflicts item truncated")
                if len(output.civilization_baseline) > 200:
                    warnings.append("civilization_baseline truncated")
            pending_warnings = warnings
            pending = _clamp_campaign_setup(output)

        wire_result = self.llm.run(
            task="campaign_setup",
            input_data={
                "init_config_json": config.model_dump_json(indent=2),
                "output_schema_json": display_schema_json(
                    CampaignSetupProposal,
                    max_lengths=CAMPAIGN_SETUP_PROMPT_TARGETS,
                ),
            },
            output_schema=CampaignSetupProposalWire,
            output_validator=validate_output,
            on_attempt_start=on_attempt_start,
            temperature=0.4,
            trace_id=trace_id,
        )
        if pending is None:
            # StubGateway compatibility.
            pending = _clamp_campaign_setup(wire_result)
        result = CampaignSetupProposal.model_validate(pending)
        self.last_warnings = pending_warnings
        return result


def _campaign_setup_accept_errors(value: CampaignSetupProposalWire) -> list[str]:
    """首轮 Accept 上限检查：A=S=产品上限，超出即反馈重试一次。"""

    errors: list[str] = []
    baseline = value.public_power_baseline
    if len(baseline.source) > 120:
        errors.append(
            f"public_power_baseline.source length {len(baseline.source)} "
            "exceeds accept limit 120; return to the prompt target 100"
        )
    if len(baseline.limitation) > 80:
        errors.append(
            f"public_power_baseline.limitation length "
            f"{len(baseline.limitation)} exceeds accept limit 80; return to "
            "the prompt target 60"
        )
    for index, conflict in enumerate(value.major_world_conflicts):
        if len(conflict) > 50:
            errors.append(
                f"major_world_conflicts[{index}] length {len(conflict)} "
                "exceeds accept limit 50; return to the prompt target 40"
            )
    if len(value.civilization_baseline) > 200:
        errors.append(
            f"civilization_baseline length {len(value.civilization_baseline)} "
            "exceeds accept limit 200; return to the prompt target 160"
        )
    return errors
