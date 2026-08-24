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
    FinalSceneFrame,
    FRAME_OPERATION_TYPES,
    StateChangeSet,
    StateChangeWarning,
    StateOperation,
    StateResolutionOutput,
    StateResolutionOutputLoose,
    apply_state_changes,
    compile_frame_operations,
)
from .schemas import CurrentState


#: 输出修正最多重试 1 次（由 Gateway 统一负责，总计最多两次模型调用）。
#: 只有结构/schema 错误才触发反馈修正；evidence 问题一律剔除并记录警告。
_MAX_SEMANTIC_RETRIES = 1

_MAX_OPERATIONS = 30

_OPERATION_ADAPTER = TypeAdapter(StateOperation)

_FRAME_ADAPTER = TypeAdapter(FinalSceneFrame)


@dataclass
class _OperationError:
    operation_id: str
    operation_type: str
    reason: str


@dataclass
class _AttemptRecord:
    decision: str = ""
    frame: FinalSceneFrame | None = None
    valid_ops: list[StateOperation] = field(default_factory=list)
    errors: list[_OperationError] = field(default_factory=list)
    warnings: list[StateChangeWarning] = field(default_factory=list)


class StateUpdater:
    """Resolve the confirmed scene into a final frame plus typed changes.

    The LLM audits the spatio-temporal flow of the prose and freezes the
    scene-ending snapshot (:class:`FinalSceneFrame`); it additionally emits
    attitude/status/resource operations. The deterministic engine compiles
    the frame into location/participant/clock operations.

    The Gateway is the only retry owner: it may call the model at most twice
    (one feedback correction) and only for structural/schema problems.
    Evidence problems never retry: unknown evidence IDs are stripped, items
    left without valid evidence are ignored with a warning.
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
        schema = schema_json(StateResolutionOutput)
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

        def validate_resolution(output: StateResolutionOutputLoose) -> None:
            record = attempt_records[current_attempt]
            record.decision = (
                output.decision
                or "State update applied without a decision; kept valid operations."
            )
            record.valid_ops = []
            record.errors = []
            record.warnings = []
            record.frame = None
            seen_ids: set[str] = set()

            extra_keys = sorted((output.model_extra or {}).keys())
            if extra_keys:
                record.errors.append(_OperationError(
                    operation_id="top_level",
                    operation_type="state_update",
                    reason=f"unexpected top-level fields: {extra_keys}",
                ))

            frame = _validate_frame(
                output.frame,
                record=record,
                valid_fact_ids=valid_fact_ids,
                valid_paragraph_ids=valid_paragraph_ids,
            )

            operations = output.operations
            if len(operations) > _MAX_OPERATIONS:
                record.warnings.append(StateChangeWarning(
                    operation_id="operations",
                    operation_type="state_update",
                    target="",
                    code="state_update_operations_truncated",
                    message=(
                        f"operations exceed {_MAX_OPERATIONS}; "
                        f"{len(operations) - _MAX_OPERATIONS} item(s) dropped"
                    ),
                ))
                operations = operations[:_MAX_OPERATIONS]

            for index, item in enumerate(operations):
                operation_id, operation_type = _item_identity(item, index)
                # Evidence tolerance is checked before strict schema
                # validation: an item without any evidence is dropped with a
                # warning instead of triggering a Gateway feedback retry.
                if _item_has_no_evidence(item):
                    record.warnings.append(StateChangeWarning(
                        operation_id=operation_id,
                        operation_type=operation_type,
                        target="",
                        code="state_update_operation_dropped",
                        message=(
                            f"{operation_id} ({operation_type}) "
                            "dropped: no valid evidence remains"
                        ),
                    ))
                    continue
                try:
                    operation = _OPERATION_ADAPTER.validate_python(item)
                except ValidationError as exc:
                    record.errors.append(_OperationError(
                        operation_id=operation_id,
                        operation_type=operation_type,
                        reason=str(exc),
                    ))
                    continue
                if operation.type in FRAME_OPERATION_TYPES:
                    record.warnings.append(StateChangeWarning(
                        operation_id=operation.operation_id,
                        operation_type=operation.type,
                        target="",
                        code="state_update_operation_compiled_by_frame",
                        message=(
                            f"{operation.operation_id} ({operation.type}) "
                            "dropped: location, participant and clock changes "
                            "are compiled from FinalSceneFrame by the engine"
                        ),
                    ))
                    continue
                if operation.operation_id.startswith("frame_"):
                    # "frame_" is the reserved namespace of the deterministic
                    # engine; a model-provided ID in it would collide with the
                    # compiled operations inside the final StateChangeSet.
                    record.warnings.append(StateChangeWarning(
                        operation_id=operation.operation_id,
                        operation_type=operation.type,
                        target="",
                        code="state_update_operation_reserved_id",
                        message=(
                            f"{operation.operation_id} ({operation.type}) "
                            "dropped: the frame_ id prefix is reserved for "
                            "engine-compiled operations"
                        ),
                    ))
                    continue
                if operation.operation_id in seen_ids:
                    record.errors.append(_OperationError(
                        operation_id=operation.operation_id,
                        operation_type=operation.type,
                        reason=(
                            f"duplicate operation_id: {operation.operation_id}"
                        ),
                    ))
                    continue
                seen_ids.add(operation.operation_id)
                _strip_unknown_evidence(
                    operation,
                    record=record,
                    valid_fact_ids=valid_fact_ids,
                    valid_paragraph_ids=valid_paragraph_ids,
                )
                if not (
                    operation.evidence_fact_ids or operation.evidence_paragraph_ids
                ):
                    record.warnings.append(StateChangeWarning(
                        operation_id=operation.operation_id,
                        operation_type=operation.type,
                        target="",
                        code="state_update_operation_dropped",
                        message=(
                            f"{operation.operation_id} ({operation.type}) "
                            "dropped: no valid evidence remains"
                        ),
                    ))
                    continue
                record.valid_ops.append(operation)

            if frame is not None:
                record.frame = frame

            if record.errors:
                raise ValueError(
                    "StateResolutionOutput 中存在无法应用的内容；"
                    + "; ".join(
                        f"{error.operation_id}[{error.operation_type}]: "
                        f"{error.reason}"
                        for error in record.errors
                    )
                )

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
                output_schema=StateResolutionOutputLoose,
                output_validator=validate_resolution,
                on_attempt_start=on_attempt_start,
                max_retries=_MAX_SEMANTIC_RETRIES,
                trace_id=trace_id,
            )
            record = attempt_records[current_attempt]
            return self._finalize(current_state, record, npc_aliases)
        except (
            LLMOutputValidationError,
            LLMOutputParseError,
        ) as exc:
            return self._salvage(exc, current_state, attempt_records, npc_aliases)

    def _finalize(
        self,
        current_state: CurrentState,
        record: _AttemptRecord,
        npc_aliases: dict[str, list[str]] | None,
    ) -> CurrentState:
        change_set, warnings = _build_change_set(current_state, record, npc_aliases)
        application = apply_state_changes(
            current_state,
            change_set,
            npc_aliases=npc_aliases,
        )
        self.last_change_set = change_set
        self.last_warnings = [
            *record.warnings,
            *warnings,
            *application.warnings,
        ]
        return application.state

    def _salvage(
        self,
        exception: Exception,
        current_state: CurrentState,
        attempt_records: dict[int, _AttemptRecord],
        npc_aliases: dict[str, list[str]] | None,
    ) -> CurrentState:
        def _pick(*, want_ops: bool) -> _AttemptRecord | None:
            for key in (2, 1):
                record = attempt_records.get(key)
                if record is None:
                    continue
                if want_ops and record.valid_ops:
                    return record
                if not want_ops and not record.valid_ops and record.frame:
                    return record
            return None

        record = _pick(want_ops=True) or _pick(want_ops=False)
        if record is None:
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

        change_set, warnings = _build_change_set(current_state, record, npc_aliases)
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
        application = apply_state_changes(
            current_state,
            change_set,
            npc_aliases=npc_aliases,
        )
        self.last_change_set = change_set
        self.last_warnings = [
            *record.warnings,
            *warnings,
            *dropped_warnings,
            *application.warnings,
        ]
        return application.state


def _build_change_set(
    current_state: CurrentState,
    record: _AttemptRecord,
    npc_aliases: dict[str, list[str]] | None,
) -> tuple[StateChangeSet, list[StateChangeWarning]]:
    """Combine frame-compiled and LLM operations into one auditable set.

    The 30-item cap constrains only the model output (enforced during
    validation); deterministic frame-compiled operations never consume that
    quota and are never truncated here.
    """

    compiled_ops: list[StateOperation] = []
    compiled_warnings: list[StateChangeWarning] = []
    if record.frame is not None:
        compiled_ops, compiled_warnings = compile_frame_operations(
            current_state,
            record.frame,
            npc_aliases=npc_aliases,
        )
    operations = [*compiled_ops, *record.valid_ops]
    return (
        StateChangeSet(decision=record.decision, operations=operations),
        compiled_warnings,
    )


def _validate_frame(
    raw_frame: Any,
    *,
    record: _AttemptRecord,
    valid_fact_ids: set[str],
    valid_paragraph_ids: set[str],
) -> FinalSceneFrame | None:
    """Validate the final frame and enforce its evidence tolerance rules."""

    if raw_frame is None:
        record.errors.append(_OperationError(
            operation_id="frame",
            operation_type="final_scene_frame",
            reason="missing required field: frame",
        ))
        return None
    try:
        frame = _FRAME_ADAPTER.validate_python(raw_frame)
    except ValidationError as exc:
        record.errors.append(_OperationError(
            operation_id="frame",
            operation_type="final_scene_frame",
            reason=str(exc),
        ))
        return None

    unknown_facts = sorted(
        set(frame.evidence_fact_ids) - valid_fact_ids,
    )
    unknown_paragraphs = sorted(
        set(frame.evidence_paragraph_ids) - valid_paragraph_ids,
    )
    if unknown_facts or unknown_paragraphs:
        record.warnings.append(StateChangeWarning(
            operation_id="frame",
            operation_type="final_scene_frame",
            target="",
            code="frame_evidence_unknown_stripped",
            message=(
                "frame evidence stripped of unknown IDs: "
                f"facts={unknown_facts}, paragraphs={unknown_paragraphs}"
            ),
        ))
        frame = frame.model_copy(update={
            "evidence_fact_ids": [
                fact_id
                for fact_id in frame.evidence_fact_ids
                if fact_id in valid_fact_ids
            ],
            "evidence_paragraph_ids": [
                paragraph_id
                for paragraph_id in frame.evidence_paragraph_ids
                if paragraph_id in valid_paragraph_ids
            ],
        })

    has_content = bool(
        frame.final_location
        or frame.day_offset
        or frame.final_phase
        or frame.active_present_npcs
        or frame.departed_npcs
    )
    if has_content and not (
        frame.evidence_fact_ids or frame.evidence_paragraph_ids
    ):
        record.warnings.append(StateChangeWarning(
            operation_id="frame",
            operation_type="final_scene_frame",
            target="",
            code="frame_ignored_no_evidence",
            message=(
                "frame ignored: no valid evidence remains after stripping "
                "unknown IDs"
            ),
        ))
        return None
    return frame


def _strip_unknown_evidence(
    operation: StateOperation,
    *,
    record: _AttemptRecord,
    valid_fact_ids: set[str],
    valid_paragraph_ids: set[str],
) -> None:
    unknown_facts = sorted(
        set(operation.evidence_fact_ids) - valid_fact_ids,
    )
    unknown_paragraphs = sorted(
        set(operation.evidence_paragraph_ids) - valid_paragraph_ids,
    )
    if not (unknown_facts or unknown_paragraphs):
        return
    record.warnings.append(StateChangeWarning(
        operation_id=operation.operation_id,
        operation_type=operation.type,
        target="",
        code="state_update_evidence_unknown_stripped",
        message=(
            f"{operation.operation_id} ({operation.type}) evidence stripped "
            f"of unknown IDs: facts={unknown_facts}, "
            f"paragraphs={unknown_paragraphs}"
        ),
    ))
    operation.evidence_fact_ids = [
        fact_id
        for fact_id in operation.evidence_fact_ids
        if fact_id in valid_fact_ids
    ]
    operation.evidence_paragraph_ids = [
        paragraph_id
        for paragraph_id in operation.evidence_paragraph_ids
        if paragraph_id in valid_paragraph_ids
    ]


def _item_identity(item: Any, index: int) -> tuple[str, str]:
    if isinstance(item, dict):
        operation_id = str(item.get("operation_id") or f"#{index}")
        operation_type = str(item.get("type") or "unknown")
    else:
        operation_id = f"#{index}"
        operation_type = "unknown"
    return operation_id, operation_type


def _item_has_no_evidence(item: Any) -> bool:
    """True when a raw operation item carries no evidence at all.

    Checked before strict schema validation so an empty-evidence item is
    tolerated (dropped with a warning) instead of being reported as a
    structural error that triggers a Gateway feedback retry.
    """

    if not isinstance(item, dict):
        return False
    fact_ids = item.get("evidence_fact_ids")
    paragraph_ids = item.get("evidence_paragraph_ids")
    return not (
        (isinstance(fact_ids, list) and any(
            str(value).strip() for value in fact_ids
        ))
        or (isinstance(paragraph_ids, list) and any(
            str(value).strip() for value in paragraph_ids
        ))
    )


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
        "## 本轮正文段落（第一手依据）\n\n"
        "以下带 ID 的正文段落是本轮确认场景的完整原文，是状态推导的主要观察材料；"
        "Scene Facts 仅为辅助压缩参考，两者冲突时以正文段落为准。"
        "状态操作必须引用 paragraph ID 或真实存在的 Scene Fact ID，不得虚构。\n\n"
        + "\n".join(lines)
        + "\n\n"
    )
