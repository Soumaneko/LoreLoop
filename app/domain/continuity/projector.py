import json
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import schema_json
from app.domain.confirmed_history import HistoryDocument

from .schemas import (
    CURRENT_STATE_ACCEPT_LIMIT,
    CURRENT_STATE_SOFT_LIMIT,
    PREMISE_ACCEPT_LIMIT,
    PREMISE_SOFT_LIMIT,
    REASON_ACCEPT_LIMIT,
    REASON_SOFT_LIMIT,
    STATE_DELTA_ACCEPT_LIMIT,
    STATE_DELTA_SOFT_LIMIT,
    SUBJECT_ACCEPT_LIMIT,
    SUBJECT_SOFT_LIMIT,
    ContinuityOperation,
    ContinuityProjection,
    ContinuityProjectionOutput,
    ContinuityProjectionOutputLoose,
)

_OPERATION_ADAPTER = TypeAdapter(ContinuityOperation)


@dataclass(frozen=True)
class ContinuityWarning:
    code: str
    message: str


@dataclass
class _AttemptRecord:
    operations: list[ContinuityOperation] = field(default_factory=list)
    warnings: list[ContinuityWarning] = field(default_factory=list)


def _accept_length_errors(operation: ContinuityOperation) -> list[str]:
    """First-attempt Accept-Limit check (H shown to the model, A accepted).

    Values above ``A`` trigger one feedback retry asking the model to return
    to the prompt target ``H``; values within ``(A, S]`` are only tolerated
    on the second attempt.
    """

    errors: list[str] = []

    def _check(field: str, value: str, limit: int) -> None:
        if len(value) > limit:
            errors.append(
                f"{field} length {len(value)} exceeds accept limit {limit}; "
                "return to the prompt target"
            )

    if operation.operation == "create":
        _check("subject", operation.item.subject, SUBJECT_ACCEPT_LIMIT)
        _check("premise", operation.item.premise, PREMISE_ACCEPT_LIMIT)
    elif operation.operation == "update":
        _check("subject", operation.subject, SUBJECT_ACCEPT_LIMIT)
        _check("state_delta", operation.state_delta, STATE_DELTA_ACCEPT_LIMIT)
        _check(
            "new_current_state",
            operation.new_current_state,
            CURRENT_STATE_ACCEPT_LIMIT,
        )
    _check("reason", operation.reason, REASON_ACCEPT_LIMIT)
    return errors


class ContinuityProjector:
    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[ContinuityWarning] = []

    def project(
        self,
        *,
        current_documents: list[HistoryDocument],
        active_items: list[Any],
        trace_id: str | None = None,
    ) -> ContinuityProjection:
        self.last_warnings = []
        confirmed_scene_documents = [
            document
            for document in current_documents
            if document.source_type == "scene_paragraph"
        ]
        if not confirmed_scene_documents:
            return ContinuityProjection(operations=[])

        valid_evidence_ids = {
            document.document_id for document in confirmed_scene_documents
        }
        active_item_ids = {item.id for item in active_items}
        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: ContinuityProjectionOutputLoose) -> None:
            raw_operations = [
                _strip_operation_text(raw) for raw in output.root
            ]
            truncation_warnings: list[ContinuityWarning] = []
            if current_attempt > 1:
                raw_operations, truncation_warnings = _soft_truncate_operations(
                    raw_operations
                )
            operations: list[ContinuityOperation] = []
            dropped_warnings: list[ContinuityWarning] = []
            retry_errors: list[str] = []
            for index, raw in enumerate(raw_operations):
                try:
                    parsed = _OPERATION_ADAPTER.validate_python(raw)
                except ValidationError as exc:
                    retry_errors.append(f"operation[{index}]: {exc}")
                    dropped_warnings.append(ContinuityWarning(
                        code="continuity_operation_dropped",
                        message=f"operation[{index}] dropped: {exc}",
                    ))
                    continue
                if current_attempt == 1:
                    # First attempt tolerates up to the Accept Limit without a
                    # retry; only fields above A trigger the single feedback
                    # correction (which asks the model to return to H).
                    accept_errors = _accept_length_errors(parsed)
                    if accept_errors:
                        retry_errors.extend(
                            f"operation[{index}]: {error}"
                            for error in accept_errors
                        )
                        continue
                operation_errors = _validate_operation(
                    parsed,
                    valid_evidence_ids=valid_evidence_ids,
                    active_item_ids=active_item_ids,
                )
                if operation_errors:
                    retry_errors.extend(
                        f"operation[{index}]: {error}"
                        for error in operation_errors
                    )
                    dropped_warnings.append(ContinuityWarning(
                        code="continuity_operation_dropped",
                        message=(
                            f"operation[{index}] "
                            f"({parsed.operation}) dropped: "
                            + "; ".join(operation_errors)
                        ),
                    ))
                    continue
                operations.append(parsed)

            item_counts: dict[str, int] = {}
            for operation in operations:
                item_id = getattr(operation, "item_id", None)
                if item_id is not None:
                    item_counts[item_id] = item_counts.get(item_id, 0) + 1
            conflict_ids = {
                item_id
                for item_id, count in item_counts.items()
                if count > 1
            }
            if conflict_ids:
                filtered: list[ContinuityOperation] = []
                for operation in operations:
                    item_id = getattr(operation, "item_id", None)
                    if item_id in conflict_ids:
                        dropped_warnings.append(ContinuityWarning(
                            code="continuity_operation_dropped",
                            message=(
                                f"operation ({operation.operation}) dropped: "
                                f"item_id appears more than once: {item_id}"
                            ),
                        ))
                    else:
                        filtered.append(operation)
                operations = filtered
                retry_errors.append(
                    "item_id appears more than once: "
                    + ", ".join(sorted(conflict_ids))
                )

            attempt_records[current_attempt] = _AttemptRecord(
                operations=operations,
                warnings=[*truncation_warnings, *dropped_warnings],
            )
            if retry_errors:
                raise ValueError("; ".join(retry_errors))

        def salvage(exception: Exception) -> ContinuityProjection:
            latest = attempt_records.get(2)
            if latest is None:
                latest = attempt_records.get(1)
            usable = attempt_records.get(2)
            if usable is None or not usable.operations:
                usable = attempt_records.get(1)
            if usable is not None and usable.operations:
                self.last_warnings = list(usable.warnings)
                return ContinuityProjection(operations=usable.operations)
            warnings = list(latest.warnings) if latest is not None else []
            warnings.append(ContinuityWarning(
                code="continuity_fallback",
                message=(
                    "Continuity projection failed; no operations applied: "
                    f"{exception}"
                ),
            ))
            self.last_warnings = warnings
            return ContinuityProjection(operations=[])

        try:
            result = cast(
                ContinuityProjectionOutputLoose,
                self.llm.run(
                    task="continuity_project",
                    input_data={
                        "confirmed_turn_json": json.dumps(
                            [
                                {
                                    "evidence_id": document.document_id,
                                    "source_type": document.source_type,
                                    "text": document.text,
                                }
                                for document in confirmed_scene_documents
                            ],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "active_continuity_json": json.dumps(
                            [
                                {
                                    "id": item.id,
                                    "kind": item.kind,
                                    "subject": item.subject,
                                    "premise": item.premise,
                                    "current_state": item.current_state,
                                    "entities": item.entities,
                                    "location": item.location,
                                    "deadline": item.deadline,
                                }
                                for item in active_items
                            ],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "output_schema_json": schema_json(
                            ContinuityProjectionOutput
                        ),
                    },
                    output_schema=ContinuityProjectionOutputLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    temperature=0.1,
                    trace_id=trace_id,
                ),
            )
            record = attempt_records[current_attempt]
            self.last_warnings = list(record.warnings)
            return ContinuityProjection(operations=record.operations)
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
        ) as exc:
            return salvage(exc)


def _validate_operation(
    operation: ContinuityOperation,
    *,
    valid_evidence_ids: set[str],
    active_item_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    unknown_evidence = set(operation.evidence_ids) - valid_evidence_ids
    if unknown_evidence:
        errors.append(
            f"references unknown evidence IDs: {sorted(unknown_evidence)}"
        )
    item_id = getattr(operation, "item_id", None)
    if item_id is not None and item_id not in active_item_ids:
        errors.append(f"references non-active item_id: {item_id}")
    return errors


def _strip_operation_text(raw: Any) -> Any:
    """统一去除自由文本字段首尾空白，避免纯空白通过 min_length。"""
    if not isinstance(raw, dict):
        return raw
    raw = dict(raw)
    operation_type = raw.get("operation")
    if operation_type == "create":
        item = raw.get("item")
        if isinstance(item, dict):
            item = dict(item)
            for key in ("subject", "premise"):
                if isinstance(item.get(key), str):
                    item[key] = item[key].strip()
            raw["item"] = item
    elif operation_type == "update":
        for key in ("subject", "state_delta", "new_current_state"):
            if isinstance(raw.get(key), str):
                raw[key] = raw[key].strip()
    if isinstance(raw.get("reason"), str):
        raw["reason"] = raw["reason"].strip()
    return raw


def _soft_truncate_operations(
    raw_operations: list[Any],
) -> tuple[list[Any], list[ContinuityWarning]]:
    result: list[Any] = []
    warnings: list[ContinuityWarning] = []
    for raw in raw_operations:
        if not isinstance(raw, dict):
            result.append(raw)
            continue
        raw = dict(raw)
        operation_type = raw.get("operation")
        if operation_type == "create":
            item = raw.get("item")
            if isinstance(item, dict):
                item = dict(item)
                _truncate_text(
                    item,
                    "subject",
                    SUBJECT_SOFT_LIMIT,
                    "create.subject",
                    warnings,
                )
                _truncate_text(
                    item,
                    "premise",
                    PREMISE_SOFT_LIMIT,
                    "create.premise",
                    warnings,
                )
                raw["item"] = item
        elif operation_type == "update":
            _truncate_text(
                raw, "subject", SUBJECT_SOFT_LIMIT, "update.subject", warnings
            )
            _truncate_text(
                raw,
                "state_delta",
                STATE_DELTA_SOFT_LIMIT,
                "update.state_delta",
                warnings,
            )
            _truncate_text(
                raw,
                "new_current_state",
                CURRENT_STATE_SOFT_LIMIT,
                "update.new_current_state",
                warnings,
            )
        _truncate_text(
            raw, "reason", REASON_SOFT_LIMIT, f"{operation_type}.reason", warnings
        )
        result.append(raw)
    return result, warnings


def _truncate_text(
    raw: dict[str, Any],
    key: str,
    soft_limit: int,
    label: str,
    warnings: list[ContinuityWarning],
) -> None:
    value = raw.get(key)
    if isinstance(value, str):
        clean = value.strip()
        raw[key] = clean
        if len(clean) > soft_limit:
            raw[key] = clean[:soft_limit]
            warnings.append(ContinuityWarning(
                code="continuity_truncated",
                message=(
                    f"{label} exceeded soft limit {soft_limit} and was "
                    f"truncated."
                ),
            ))
