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
from app.domain.context_facts.schemas import (
    ContextExtractorInput,
    ContextFactDraft,
    ContextFactDraftSet,
    ContextFactDraftSetLoose,
    ContextFactSet,
)
from app.domain.views import (
    project_current_state,
    project_npc_profiles,
    project_scene_summaries,
)

from .views import (
    project_context_history,
    project_continuity,
    project_settings,
)


@dataclass(frozen=True)
class ContextFactWarning:
    code: str
    message: str


@dataclass
class _AttemptRecord:
    turn_focus: str = ""
    facts: list[ContextFactDraft] = field(default_factory=list)
    warnings: list[ContextFactWarning] = field(default_factory=list)


_FACT_ADAPTER = TypeAdapter(ContextFactDraft)


def _parse_draft(raw: Any) -> ContextFactDraft:
    if isinstance(raw, dict):
        cleaned = dict(raw)
        for key in ("holder", "source", "fact"):
            value = cleaned.get(key)
            if isinstance(value, str):
                cleaned[key] = value.strip()
        raw = cleaned
    draft = _FACT_ADAPTER.validate_python(raw)
    if not draft.holder.strip() or not draft.source.strip() or not draft.fact.strip():
        raise ValueError("holder, source, and fact must be non-blank")
    return draft


def _known_evidence_ids(input_data: ContextExtractorInput) -> set[str]:
    return {
        evidence_id
        for hit in input_data.history_evidence.hits
        for evidence_id in [
            hit.evidence_id,
            *hit.supporting_evidence_ids,
            *(item.evidence_id for item in hit.context_before),
            *(item.evidence_id for item in hit.context_after),
        ]
    }


def _collect_record(
    output: Any,
    known_evidence_ids: set[str],
) -> tuple[_AttemptRecord, list[str]]:
    draft = cast(ContextFactDraftSetLoose, output)
    warnings: list[ContextFactWarning] = []
    retry_errors: list[str] = []

    turn_focus = (draft.turn_focus or "").strip()
    if not turn_focus:
        retry_errors.append("turn_focus must be non-empty")

    if not isinstance(draft.facts, list):
        retry_errors.append("facts must be a list")
        facts: list[ContextFactDraft] = []
    else:
        if len(draft.facts) > 12:
            retry_errors.append("facts must have at most 12 items")
            warnings.append(ContextFactWarning(
                code="context_fact_count",
                message=(
                    f"facts contained {len(draft.facts)} items; keeping first 12"
                ),
            ))
        facts = []
        for index, raw in enumerate(draft.facts[:12]):
            try:
                fact = _parse_draft(raw)
            except (ValidationError, ValueError) as exc:
                retry_errors.append(f"fact[{index}]: {exc}")
                warnings.append(ContextFactWarning(
                    code="context_fact_dropped",
                    message=f"fact[{index}] dropped: {exc}",
                ))
                continue
            unknown = sorted(
                evidence_id
                for evidence_id in fact.evidence_ids
                if evidence_id not in known_evidence_ids
            )
            if unknown:
                retry_errors.append(
                    f"fact[{index}] unknown evidence IDs: {unknown}"
                )
                warnings.append(ContextFactWarning(
                    code="context_fact_dropped",
                    message=(
                        f"fact[{index}] dropped: unknown evidence IDs {unknown}"
                    ),
                ))
                continue
            facts.append(fact)

    return _AttemptRecord(
        turn_focus=turn_focus,
        facts=facts,
        warnings=warnings,
    ), retry_errors


def _build_result(
    record: _AttemptRecord,
    known_evidence_ids: set[str],
) -> ContextFactSet:
    return ContextFactSet.from_drafts(
        turn_focus=record.turn_focus or "上下文事实提取失败",
        facts=record.facts,
        known_evidence_ids=known_evidence_ids,
    )


class ContextExtractor:
    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[ContextFactWarning] = []
        self.last_error: str | None = None
        self.last_salvaged: bool = False

    def extract(
        self,
        input_data: ContextExtractorInput,
        *,
        trace_id: str | None = None,
    ) -> ContextFactSet:
        self.last_warnings = []
        self.last_error = None
        self.last_salvaged = False
        schema = schema_json(ContextFactDraftSet)
        known_evidence_ids = _known_evidence_ids(input_data)
        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: ContextFactDraftSetLoose) -> None:
            record, retry_errors = _collect_record(output, known_evidence_ids)
            attempt_records[current_attempt] = record
            if retry_errors:
                raise ValueError("; ".join(retry_errors))

        try:
            result = cast(
                ContextFactDraftSetLoose,
                self.llm.run(
                    task="context_extract",
                    input_data={
                        "user_input": input_data.user_input,
                        "core_memory_json": input_data.core_memory.model_dump_json(indent=2),
                        "current_state_json": project_current_state(input_data.current_state),
                        "scene_summaries_json": project_scene_summaries(input_data.scene_summaries),
                        "confirmed_history_json": project_context_history(
                            input_data.history_evidence
                        ),
                        "continuity_json": project_continuity(
                            input_data.continuity
                        ),
                        "settings_json": project_settings(
                            input_data.settings
                        ),
                        "npc_profiles_json": project_npc_profiles(input_data.npc_profiles),
                        "output_schema_json": schema,
                    },
                    output_schema=ContextFactDraftSetLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    temperature=0.2,
                    trace_id=trace_id,
                ),
            )
            if current_attempt in attempt_records:
                record = attempt_records[current_attempt]
            else:
                # StubGateway compatibility: real Gateway calls output_validator;
                # test stubs may return directly without invoking it.
                record, _ = _collect_record(result, known_evidence_ids)
            self.last_warnings = list(record.warnings)
            return _build_result(record, known_evidence_ids)
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
        ) as exc:
            return self._salvage(attempt_records, known_evidence_ids, exc)

    def _salvage(
        self,
        attempt_records: dict[int, _AttemptRecord],
        known_evidence_ids: set[str],
        exception: Exception,
    ) -> ContextFactSet:
        latest = attempt_records.get(2)
        if latest is None:
            latest = attempt_records.get(1)
        usable = attempt_records.get(2)
        if usable is None or (not usable.turn_focus and not usable.facts):
            usable = attempt_records.get(1)
        if usable is not None and (usable.turn_focus or usable.facts):
            warnings = list(usable.warnings)
            if latest is not None and latest is not usable:
                for warning in latest.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
            self.last_warnings = warnings
            self.last_salvaged = True
            return _build_result(usable, known_evidence_ids)

        warnings: list[ContextFactWarning] = []
        for record in attempt_records.values():
            for warning in record.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        warnings.append(ContextFactWarning(
            code="context_extract_failed",
            message=f"Context extraction failed; no usable facts were generated: {exception}",
        ))
        self.last_warnings = warnings
        self.last_error = str(exception)
        return ContextFactSet(turn_focus="上下文事实提取失败", facts=[])
