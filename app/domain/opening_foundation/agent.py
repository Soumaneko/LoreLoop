from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.llm import LLMGateway
from app.core.llm.schema import display_schema_json
from app.domain.core_memory import CoreMemory
from app.domain.current_state.schemas import InventoryItem

from .schemas import OpeningFoundation

#: 三级长度契约：name 为 strict_name（H=A=产品上限，无缓冲）；
#: description / opening situation 为 soft_text（A=S=产品上限，Prompt 目标 H 下调）。
OPENING_FOUNDATION_PROMPT_TARGETS = {
    ("starting_location", "description"): 480,
    "protagonist_opening_situation": 640,
}
_DESCRIPTION_ACCEPT_LIMIT = 600
_SITUATION_ACCEPT_LIMIT = 800


class _OpeningLocationWire(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)

    @field_validator("name", "description")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean


class _InitialResourcesWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory: list[InventoryItem] = Field(default_factory=list, max_length=6)
    money: int | None = Field(default=None, ge=0)
    fixed_assets: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("fixed_assets")
    @classmethod
    def normalize_fixed_assets(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(
            value.strip() for value in values if value.strip()
        ))


class OpeningFoundationWire(BaseModel):
    """宽松 wire：soft_text 字段不设 maxLength，由 output_validator 分流。

    非长度约束（version 字面量、资源条数/资产去重等）原样保留，
    最大长度之外的错误仍走 output_validator 重试。
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    starting_location: _OpeningLocationWire
    protagonist_opening_situation: str = Field(min_length=1)
    initial_resources: _InitialResourcesWire

    @field_validator("protagonist_opening_situation")
    @classmethod
    def normalize_situation(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean


class OpeningFoundationAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[str] = []

    def generate(
        self,
        *,
        core_memory: CoreMemory,
        trace_id: str | None = None,
    ) -> OpeningFoundation:
        current_attempt = 0
        pending: dict[str, Any] | None = None
        pending_warnings: list[str] = []

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: OpeningFoundationWire) -> None:
            nonlocal pending, pending_warnings
            warnings: list[str] = []
            description = output.starting_location.description.strip()
            situation = output.protagonist_opening_situation.strip()
            if current_attempt == 1:
                errors: list[str] = []
                if len(description) > _DESCRIPTION_ACCEPT_LIMIT:
                    errors.append(
                        f"starting_location.description length "
                        f"{len(description)} exceeds accept limit "
                        f"{_DESCRIPTION_ACCEPT_LIMIT}; return to the prompt "
                        "target"
                    )
                if len(situation) > _SITUATION_ACCEPT_LIMIT:
                    errors.append(
                        f"protagonist_opening_situation length "
                        f"{len(situation)} exceeds accept limit "
                        f"{_SITUATION_ACCEPT_LIMIT}; return to the prompt "
                        "target"
                    )
                if errors:
                    raise ValueError("; ".join(errors))
            else:
                if len(description) > _DESCRIPTION_ACCEPT_LIMIT:
                    warnings.append(
                        "starting_location.description truncated"
                    )
                    description = description[:_DESCRIPTION_ACCEPT_LIMIT]
                if len(situation) > _SITUATION_ACCEPT_LIMIT:
                    warnings.append(
                        "protagonist_opening_situation truncated"
                    )
                    situation = situation[:_SITUATION_ACCEPT_LIMIT]
            pending_warnings = warnings
            pending = {
                "version": output.version,
                "starting_location": {
                    "name": output.starting_location.name,
                    "description": description,
                },
                "protagonist_opening_situation": situation,
                "initial_resources": output.initial_resources.model_dump(),
            }

        wire_result = self.llm.run(
            task="opening_foundation",
            input_data={
                "core_memory_json": core_memory.model_dump_json(indent=2),
                "output_schema_json": display_schema_json(
                    OpeningFoundation,
                    max_lengths=OPENING_FOUNDATION_PROMPT_TARGETS,
                ),
            },
            output_schema=OpeningFoundationWire,
            output_validator=validate_output,
            on_attempt_start=on_attempt_start,
            temperature=0.7,
            trace_id=trace_id,
        )
        if pending is None:
            # StubGateway compatibility: stubs return without invoking
            # output_validator.
            pending = wire_result.model_dump()
        result = OpeningFoundation.model_validate(pending)
        self.last_warnings = pending_warnings
        return result
