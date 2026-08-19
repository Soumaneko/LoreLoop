import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import schema_json
from app.domain.core_memory.schemas import CoreMemory

from .changes import (
    StateChangeSet,
    StateChangeSetLoose,
    StateChangeWarning,
    StateOperation,
    apply_state_changes,
)
from .schemas import CurrentState


#: 输出修正最多重试 1 次（由 Gateway 统一负责，总计最多两次模型调用）。
_MAX_SEMANTIC_RETRIES = 1

_MAX_OPERATIONS = 30

_OPERATION_ADAPTER = TypeAdapter(StateOperation)


@dataclass
class _OperationError:
    operation_id: str
    operation_type: str
    reason: str


@dataclass
class _AttemptRecord:
    decision: str = ""
    valid_ops: list[StateOperation] = field(default_factory=list)
    errors: list[_OperationError] = field(default_factory=list)


class StateUpdater:
    """Compile Scene Facts into typed changes and apply them deterministically.

    The Gateway is the only retry owner: it may call the model at most twice
    (one feedback correction). Per-operation schema/evidence errors are
    collected in ``output_validator``; when the Gateway exhausts retries, the
    updater salvages the valid subset from the second attempt when possible,
    otherwise from the first attempt, and only falls back to the previous
    state when neither attempt has a valid operation.
    """

    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_change_set = StateChangeSet(
            decision="State update has not run yet."
        )
        self.last_warnings = []

    def update(
        self,
        current_state: CurrentState,
        scene_facts: str,
        *,
        core_memory: CoreMemory,
        npc_aliases: dict[str, list[str]] | None = None,
        paragraph_texts: dict[str, str] | None = None,
        trace_id: str | None = None,
    ) -> CurrentState:
        self.last_warnings = []
        schema = schema_json(StateChangeSet)
        valid_fact_ids = _scene_fact_ids(scene_facts)
        valid_paragraph_ids = set(paragraph_texts or {})
        paragraph_section = _paragraph_evidence_section(paragraph_texts)
        validation_feedback = "无（若这是修正输出，请以附加校验错误为准）。"

        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt
            attempt_records.setdefault(attempt, _AttemptRecord())

        def validate_operations(output: StateChangeSetLoose) -> None:
            record = attempt_records[current_attempt]
            record.decision = (
                output.decision
                or "State update applied without a decision; kept valid operations."
            )
            record.valid_ops = []
            record.errors = []
            seen_ids: set[str] = set()

            extra_keys = sorted((output.model_extra or {}).keys())
            if extra_keys:
                record.errors.append(_OperationError(
                    operation_id="top_level",
                    operation_type="state_update",
                    reason=f"unexpected top-level fields: {extra_keys}",
                ))

            operations = output.operations
            if len(operations) > _MAX_OPERATIONS:
                record.errors.append(_OperationError(
                    operation_id="operations",
                    operation_type="state_update",
                    reason=(
                        f"operations exceed {_MAX_OPERATIONS}; "
                        f"{len(operations) - _MAX_OPERATIONS} item(s) dropped"
                    ),
                ))
                operations = operations[:_MAX_OPERATIONS]

            for index, item in enumerate(operations):
                operation_id, operation_type = _item_identity(item, index)
                try:
                    operation = _OPERATION_ADAPTER.validate_python(item)
                except ValidationError as exc:
                    record.errors.append(_OperationError(
                        operation_id=operation_id,
                        operation_type=operation_type,
                        reason=str(exc),
                    ))
                    continue
                error = _validate_operation(
                    operation,
                    valid_fact_ids=valid_fact_ids,
                    valid_paragraph_ids=valid_paragraph_ids,
                    seen_ids=seen_ids,
                )
                if error is not None:
                    record.errors.append(error)
                    continue
                record.valid_ops.append(operation)

            if record.errors:
                raise ValueError(
                    "StateChangeSet 中存在无法应用的操作；"
                    + "; ".join(
                        f"{error.operation_id}[{error.operation_type}]: "
                        f"{error.reason}"
                        for error in record.errors
                    )
                )

        def salvage(exception: Exception) -> CurrentState:
            record = attempt_records.get(2)
            if record is None or not record.valid_ops:
                record = attempt_records.get(1)
            if record is None or not record.valid_ops:
                error_source = attempt_records.get(2) or attempt_records.get(1)
                reason = "; ".join(
                    f"{error.operation_id}[{error.operation_type}]: "
                    f"{error.reason}"
                    for error in (error_source.errors if error_source else [])
                ) or str(exception)
                self.last_change_set = StateChangeSet(
                    decision="State update failed; kept the previous state."
                )
                self.last_warnings = [
                    StateChangeWarning(
                        operation_id="fallback",
                        operation_type="state_update",
                        target="",
                        code="state_update_fallback",
                        message=(
                            "State update fell back to the previous state: "
                            f"{reason}"
                        ),
                    )
                ]
                return current_state

            change_set = StateChangeSet(
                decision=record.decision,
                operations=record.valid_ops,
            )
            application = apply_state_changes(
                current_state,
                change_set,
                npc_aliases=npc_aliases,
            )
            dropped_warnings = [
                StateChangeWarning(
                    operation_id=error.operation_id,
                    operation_type=error.operation_type,
                    target="",
                    code="state_update_operation_dropped",
                    message=(
                        f"{error.operation_id} ({error.operation_type}) "
                        f"dropped: {error.reason}"
                    ),
                )
                for error in record.errors
            ]
            self.last_change_set = change_set
            self.last_warnings = [*dropped_warnings, *application.warnings]
            return application.state

        try:
            self.llm.run(
                task="state_update",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "current_state_json": current_state.model_dump_json(indent=2),
                    "scene_facts_json": scene_facts,
                    "scene_paragraphs_section": paragraph_section,
                    "validation_feedback": validation_feedback,
                    "output_schema_json": schema,
                },
                output_schema=StateChangeSetLoose,
                output_validator=validate_operations,
                on_attempt_start=on_attempt_start,
                max_retries=_MAX_SEMANTIC_RETRIES,
                trace_id=trace_id,
            )
            record = attempt_records[current_attempt]
            change_set = StateChangeSet(
                decision=record.decision,
                operations=record.valid_ops,
            )
            application = apply_state_changes(
                current_state,
                change_set,
                npc_aliases=npc_aliases,
            )
            self.last_change_set = change_set
            self.last_warnings = application.warnings
            return application.state
        except (
            LLMOutputValidationError,
            LLMOutputParseError,
        ) as exc:
            return salvage(exc)


def _item_identity(item: Any, index: int) -> tuple[str, str]:
    if isinstance(item, dict):
        operation_id = str(item.get("operation_id") or f"#{index}")
        operation_type = str(item.get("type") or "unknown")
    else:
        operation_id = f"#{index}"
        operation_type = "unknown"
    return operation_id, operation_type


def _validate_operation(
    operation: StateOperation,
    *,
    valid_fact_ids: set[str],
    valid_paragraph_ids: set[str],
    seen_ids: set[str],
) -> _OperationError | None:
    operation_id = operation.operation_id
    operation_type = operation.type

    unknown_facts = set(operation.evidence_fact_ids) - valid_fact_ids
    if unknown_facts:
        return _OperationError(
            operation_id=operation_id,
            operation_type=operation_type,
            reason=(
                f"references unknown Scene Fact IDs: {sorted(unknown_facts)}"
            ),
        )
    unknown_paragraphs = set(operation.evidence_paragraph_ids) - valid_paragraph_ids
    if unknown_paragraphs:
        return _OperationError(
            operation_id=operation_id,
            operation_type=operation_type,
            reason=(
                f"references unknown paragraph IDs: {sorted(unknown_paragraphs)}"
            ),
        )
    if operation_id in seen_ids:
        return _OperationError(
            operation_id=operation_id,
            operation_type=operation_type,
            reason=f"duplicate operation_id: {operation_id}",
        )
    seen_ids.add(operation_id)
    return None


def _scene_fact_ids(scene_facts: str) -> set[str]:
    try:
        data = json.loads(scene_facts)
    except (json.JSONDecodeError, TypeError):
        return set()
    return {
        str(item.get("fact_id"))
        for item in data.get("facts", [])
        if isinstance(item, dict) and item.get("fact_id")
    }


def _paragraph_evidence_section(
    paragraph_texts: dict[str, str] | None,
) -> str:
    if not paragraph_texts:
        return ""
    lines = [
        f"- {paragraph_id}: {text}"
        for paragraph_id, text in paragraph_texts.items()
    ]
    return (
        "## 本轮正文段落证据\n\n"
        "Scene Facts 为空，以下正文段落是当前轮次的场景依据；"
        "状态操作必须引用 paragraph ID，不得虚构事实。\n\n"
        + "\n".join(lines)
        + "\n\n"
    )
