from __future__ import annotations

import json
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.llm import LLMGateway
from app.core.llm.schema import display_schema_json
from app.domain.core_memory.schemas import CoreMemory

from .schemas import SettingCreateOutput, SettingKind, WorldSetting

# soft_text：H=80，A=S=产品上限 100；name 为 strict_name（保持 ≤10 严格校验）。
SETTING_PROMPT_TARGETS = {"summary": 80, "public_facts": 80, "hidden_facts": 80}


class _SettingCreateWire(BaseModel):
    """宽松 wire：仅 soft_text 文本放宽，其余约束（唯一性、事实交叉、
    至少一条事实、extra 封锁）原样保留，均在 Gateway 内校验。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1)
    public_facts: list[str] = Field(default_factory=list, max_length=5)
    hidden_facts: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("public_facts", "hidden_facts")
    @classmethod
    def _clean_facts(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("setting facts must be unique")
        return cleaned

    @model_validator(mode="after")
    def _require_concrete_facts(self) -> "_SettingCreateWire":
        if not self.public_facts and not self.hidden_facts:
            raise ValueError("setting requires at least one concrete fact")
        overlap = set(self.public_facts) & set(self.hidden_facts)
        if overlap:
            raise ValueError("a setting fact cannot be both public and hidden")
        return self


class WorldSettingCreator:
    def __init__(self, llm: LLMGateway):
        self._llm = llm
        self.last_warnings: list[str] = []

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
        current_attempt = 0
        pending: dict[str, Any] | None = None
        pending_warnings: list[str] = []
        self.last_warnings = []

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: _SettingCreateWire) -> None:
            nonlocal pending, pending_warnings
            summary = output.summary.strip()
            facts = [fact.strip() for fact in output.public_facts]
            hidden = [fact.strip() for fact in output.hidden_facts]
            warnings: list[str] = []
            if current_attempt == 1:
                errors = []
                if len(summary) > 100:
                    errors.append(
                        f"summary length {len(summary)} exceeds accept limit "
                        "100; return to the prompt target 80"
                    )
                errors.extend(
                    f"public_facts[{index}] length {len(fact)} exceeds "
                    "accept limit 100"
                    for index, fact in enumerate(facts)
                    if len(fact) > 100
                )
                errors.extend(
                    f"hidden_facts[{index}] length {len(fact)} exceeds "
                    "accept limit 100"
                    for index, fact in enumerate(hidden)
                    if len(fact) > 100
                )
                if errors:
                    raise ValueError("; ".join(errors))
            else:
                if len(summary) > 100:
                    warnings.append("summary truncated to 100")
                    summary = summary[:100]
                truncated_facts = False
                truncated_hidden = False
                new_facts = []
                for fact in facts:
                    if len(fact) > 100:
                        truncated_facts = True
                        new_facts.append(fact[:100])
                    else:
                        new_facts.append(fact)
                facts = new_facts
                new_hidden = []
                for fact in hidden:
                    if len(fact) > 100:
                        truncated_hidden = True
                        new_hidden.append(fact[:100])
                    else:
                        new_hidden.append(fact)
                hidden = new_hidden
                if truncated_facts:
                    warnings.append("public_facts truncated to 100")
                if truncated_hidden:
                    warnings.append("hidden_facts truncated to 100")
            pending_warnings = warnings
            pending = {
                "name": output.name.strip(),
                "summary": summary,
                "public_facts": facts,
                "hidden_facts": hidden,
            }

        wire_result = cast(
            _SettingCreateWire,
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
                    "output_schema_json": display_schema_json(
                        SettingCreateOutput,
                        max_lengths=SETTING_PROMPT_TARGETS,
                    ),
                },
                output_schema=_SettingCreateWire,
                output_validator=validate_output,
                on_attempt_start=on_attempt_start,
                temperature=0.9,
                trace_id=trace_id,
            ),
        )
        if pending is None:
            # StubGateway compatibility.
            pending = {
                "name": wire_result.name,
                "summary": wire_result.summary,
                "public_facts": wire_result.public_facts,
                "hidden_facts": wire_result.hidden_facts,
            }
            self.last_warnings = []
        else:
            self.last_warnings = pending_warnings
        output = SettingCreateOutput.model_validate(pending)
        return WorldSetting(
            kind=kind,
            **output.model_dump(),
            related_setting_ids=[],
            created_turn=turn_index,
        )
