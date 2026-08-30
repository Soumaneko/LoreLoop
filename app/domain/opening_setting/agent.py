from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm import LLMGateway
from app.core.llm.schema import display_schema_json
from app.domain.core_memory import CoreMemory
from app.domain.opening_foundation import OpeningFoundation

from .schemas import OpeningSettingSpecs, SettingKind

#: soft_text：H=100，A=S=产品上限 120。
SETTING_DESCRIPTION_PROMPT_TARGET = 100


class _OpeningSettingSpecWire(BaseModel):
    """宽松 wire：setting_description 不设上限，由 output_validator 分流。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: SettingKind
    setting_description: str = Field(min_length=1)


class OpeningSettingSpecsWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specs: list[_OpeningSettingSpecWire] = Field(min_length=2, max_length=4)


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
        current_attempt = 0
        pending: list[_OpeningSettingSpecWire] | None = None
        self.last_warnings = []

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: OpeningSettingSpecsWire) -> None:
            nonlocal pending
            warnings: list[str] = []
            if current_attempt == 1:
                errors = [
                    f"specs[{index}].setting_description length "
                    f"{len(spec.setting_description)} exceeds accept limit 120"
                    for index, spec in enumerate(output.specs)
                    if len(spec.setting_description.strip()) > 120
                ]
                if errors:
                    raise ValueError("; ".join(errors))
            else:
                clamped = []
                for index, spec in enumerate(output.specs):
                    text = spec.setting_description.strip()
                    if len(text) > 120:
                        warnings.append(
                            f"specs[{index}].setting_description truncated"
                        )
                        text = text[:120]
                    clamped.append({
                        "kind": spec.kind,
                        "setting_description": text,
                    })
                pending = clamped
                self.last_warnings = warnings
                return
            pending = [
                {
                    "kind": spec.kind,
                    "setting_description": spec.setting_description,
                }
                for spec in output.specs
            ]

        wire_result = self.llm.run(
            task="opening_setting_requirements",
            input_data={
                "opening_foundation_json": foundation.model_dump_json(indent=2),
                "core_memory_json": core_memory.model_dump_json(indent=2),
                "output_schema_json": display_schema_json(
                    OpeningSettingSpecs,
                    max_lengths={
                        ("specs", "setting_description"):
                            SETTING_DESCRIPTION_PROMPT_TARGET
                    },
                ),
            },
            output_schema=OpeningSettingSpecsWire,
            output_validator=validate_output,
            on_attempt_start=on_attempt_start,
            temperature=0.7,
            trace_id=trace_id,
        )
        assert pending is not None, "output_validator not called"
        return OpeningSettingSpecs.model_validate({"specs": pending})
