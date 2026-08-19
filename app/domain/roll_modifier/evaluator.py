from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import schema_json
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.roll_modifier.schemas import (
    LLMRollModifier,
    LLMRollModifierBatch,
    LLMRollModifierBatchLoose,
    RollModifier,
    RollModifierBatch,
)
from app.domain.views import project_action_parse, project_current_state


@dataclass(frozen=True)
class RollModifierWarning:
    code: str
    message: str


@dataclass
class _AttemptRecord:
    modifiers: list[RollModifier] = field(default_factory=list)
    warnings: list[RollModifierWarning] = field(default_factory=list)


_MODIFIER_ADAPTER = TypeAdapter(LLMRollModifier)


def _parse_modifier(raw: Any) -> LLMRollModifier:
    if isinstance(raw, dict):
        cleaned = dict(raw)
        for key in ("action_id", "label", "reason"):
            value = cleaned.get(key)
            if isinstance(value, str):
                cleaned[key] = value.strip()
        raw = cleaned
    modifier = _MODIFIER_ADAPTER.validate_python(raw)
    if not modifier.action_id.strip():
        raise ValueError("action_id must be non-blank")
    if not modifier.label.strip() or not modifier.reason.strip():
        raise ValueError("label and reason must be non-blank")
    return modifier


def _collect_record(
    output: LLMRollModifierBatchLoose,
    action_ids: set[str],
) -> tuple[_AttemptRecord, list[str]]:
    warnings: list[RollModifierWarning] = []
    retry_errors: list[str] = []

    if not isinstance(output.modifiers, list):
        return _AttemptRecord(warnings=warnings), ["modifiers must be a list"]

    grouped: dict[str, list[LLMRollModifier]] = defaultdict(list)
    for index, raw in enumerate(output.modifiers):
        try:
            modifier = _parse_modifier(raw)
        except (ValidationError, ValueError) as exc:
            retry_errors.append(f"modifiers[{index}]: {exc}")
            warnings.append(RollModifierWarning(
                code="roll_modifier_dropped",
                message=f"modifiers[{index}] dropped: {exc}",
            ))
            continue
        if modifier.action_id not in action_ids:
            retry_errors.append(
                f"modifiers[{index}] unknown action_id: {modifier.action_id}"
            )
            warnings.append(RollModifierWarning(
                code="roll_modifier_dropped",
                message=(
                    f"modifiers[{index}] dropped: unknown action_id "
                    f"{modifier.action_id}"
                ),
            ))
            continue
        grouped[modifier.action_id].append(modifier)

    validated: list[RollModifier] = []
    for action_id in sorted(grouped):
        action_modifiers = sorted(
            grouped[action_id],
            key=lambda item: abs(item.value),
            reverse=True,
        )
        if len(action_modifiers) > 2:
            retry_errors.append(
                f"action {action_id} has more than 2 modifiers"
            )
            for extra in action_modifiers[2:]:
                warnings.append(RollModifierWarning(
                    code="roll_modifier_dropped",
                    message=(
                        f"action {action_id} modifier {extra.label} dropped: "
                        "more than 2 modifiers"
                    ),
                ))
            action_modifiers = action_modifiers[:2]

        total = sum(modifier.value for modifier in action_modifiers)
        if abs(total) > 8:
            retry_errors.append(
                f"modifier total for action {action_id} exceeds 8"
            )
            if len(action_modifiers) == 2:
                first, second = action_modifiers
                dropped = second if abs(first.value) >= abs(second.value) else first
                warnings.append(RollModifierWarning(
                    code="roll_modifier_dropped",
                    message=(
                        f"action {action_id} modifier {dropped.label} dropped: "
                        "total exceeds 8"
                    ),
                ))
                action_modifiers = [
                    modifier
                    for modifier in action_modifiers
                    if modifier is not dropped
                ]
                total = sum(modifier.value for modifier in action_modifiers)
                if abs(total) > 8:
                    warnings.append(RollModifierWarning(
                        code="roll_modifier_dropped",
                        message=(
                            f"action {action_id} remaining modifiers still "
                            "exceed 8; dropped all"
                        ),
                    ))
                    action_modifiers = []
        validated.extend(
            RollModifier(
                action_id=modifier.action_id,
                source="situational",
                label=modifier.label,
                reason=modifier.reason,
                value=modifier.value,
            )
            for modifier in action_modifiers
        )

    return _AttemptRecord(
        modifiers=validated,
        warnings=warnings,
    ), retry_errors


class RollModifierEvaluator:
    def __init__(self, gateway: LLMGateway):
        self.gateway = gateway
        self.last_warnings: list[RollModifierWarning] = []

    def evaluate(
        self,
        parse_result: ActionParseResult,
        current_state: CurrentState,
        core_memory: CoreMemory,
        *,
        roll_actions: list[AtomicAction] | None = None,
        trace_id: str | None = None,
    ) -> RollModifierBatch:
        self.last_warnings = []
        actions = roll_actions if roll_actions is not None else [
            action for action in parse_result.atomic_actions if action.likely_needs_roll
        ]
        action_ids = {action.action_id for action in actions}
        if not action_ids:
            return RollModifierBatch()

        roll_parse_result = parse_result.model_copy(
            update={"atomic_actions": actions},
            deep=True,
        )
        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: LLMRollModifierBatchLoose) -> None:
            record, retry_errors = _collect_record(output, action_ids)
            attempt_records[current_attempt] = record
            if retry_errors:
                raise ValueError("; ".join(retry_errors))

        try:
            result = cast(
                LLMRollModifierBatchLoose,
                self.gateway.run(
                    task="roll_modifier",
                    input_data={
                        "core_memory_json": core_memory.model_dump_json(indent=2),
                        "current_state_json": project_current_state(current_state),
                        "action_parse_result_json": project_action_parse(roll_parse_result),
                        "output_schema_json": schema_json(LLMRollModifierBatch),
                    },
                    output_schema=LLMRollModifierBatchLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    trace_id=trace_id,
                ),
            )
            if current_attempt in attempt_records:
                record = attempt_records[current_attempt]
            else:
                # StubGateway compatibility.
                record, _ = _collect_record(result, action_ids)
            self.last_warnings = list(record.warnings)
            return RollModifierBatch(modifiers=record.modifiers)
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
        ) as exc:
            return self._salvage(attempt_records, exc)

    def _salvage(
        self,
        attempt_records: dict[int, _AttemptRecord],
        exception: Exception,
    ) -> RollModifierBatch:
        latest = attempt_records.get(2)
        if latest is None:
            latest = attempt_records.get(1)
        usable = attempt_records.get(2)
        if usable is None or not usable.modifiers:
            first = attempt_records.get(1)
            if first is not None and first.modifiers:
                usable = first
        if usable is not None and usable.modifiers:
            warnings = list(usable.warnings)
            if latest is not None and latest is not usable:
                for warning in latest.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
            self.last_warnings = warnings
            return RollModifierBatch(modifiers=usable.modifiers)

        warnings: list[RollModifierWarning] = []
        for record in attempt_records.values():
            for warning in record.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        warnings.append(RollModifierWarning(
            code="roll_modifier_fallback",
            message=(
                "Roll modifier evaluation failed; no modifiers were generated: "
                f"{exception}"
            ),
        ))
        self.last_warnings = warnings
        return RollModifierBatch()
