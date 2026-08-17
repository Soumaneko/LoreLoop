import json
import re
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
    LLMProviderError,
)
from app.core.llm.schema import schema_json
from app.domain.npc_profile.schemas import NPCProfileDirectorView

from .schemas import (
    CanonEntityReference,
    NarrativeCanonQueryDraft,
    NarrativeCanonQueryDraftSet,
    NarrativeCanonQueryDraftSetLoose,
    NarrativeCanonQuerySet,
)


@dataclass(frozen=True)
class CanonQueryWarning:
    code: str
    message: str


@dataclass
class _AttemptRecord:
    thought_process: str = ""
    queries: list[NarrativeCanonQueryDraft] = field(default_factory=list)
    warnings: list[CanonQueryWarning] = field(default_factory=list)


_QUERY_ADAPTER = TypeAdapter(NarrativeCanonQueryDraft)


def _parse_query(raw: Any) -> NarrativeCanonQueryDraft:
    if isinstance(raw, dict) and isinstance(raw.get("claim"), str):
        raw = {**raw, "claim": raw["claim"].strip()}
    return _QUERY_ADAPTER.validate_python(raw)


def _collect_record(
    output: NarrativeCanonQueryDraftSetLoose,
) -> tuple[_AttemptRecord, list[str]]:
    warnings: list[CanonQueryWarning] = []
    retry_errors: list[str] = []

    thought_process = (output.thought_process or "").strip()
    if not thought_process:
        retry_errors.append("thought_process must be non-empty")

    if not isinstance(output.queries, list):
        return _AttemptRecord(
            thought_process=thought_process,
            warnings=warnings,
        ), ["queries must be a list"]

    if len(output.queries) > 12:
        retry_errors.append("queries must have at most 12 items")
        warnings.append(CanonQueryWarning(
            code="canon_query_dropped",
            message=(
                f"queries contained {len(output.queries)} items; keeping first 12"
            ),
        ))

    queries: list[NarrativeCanonQueryDraft] = []
    for index, raw in enumerate(output.queries[:12]):
        try:
            query = _parse_query(raw)
        except (ValidationError, ValueError) as exc:
            retry_errors.append(f"queries[{index}]: {exc}")
            warnings.append(CanonQueryWarning(
                code="canon_query_dropped",
                message=f"queries[{index}] dropped: {exc}",
            ))
            continue
        queries.append(query)

    return _AttemptRecord(
        thought_process=thought_process,
        queries=queries,
        warnings=warnings,
    ), retry_errors


def _build_result(record: _AttemptRecord) -> NarrativeCanonQuerySet:
    return NarrativeCanonQuerySet.from_drafts(
        thought_process=record.thought_process or "无可用查询",
        drafts=record.queries,
    )


class NarrativeCanonQueryAgent:
    """Extract canon lookup requests from draft prose without retrieving or judging them."""

    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[CanonQueryWarning] = []

    def generate(
        self,
        *,
        scene_text: str,
        player_name: str,
        npc_profiles: list[NPCProfileDirectorView],
        trace_id: str | None = None,
    ) -> NarrativeCanonQuerySet:
        self.last_warnings = []
        paragraphs = _split_paragraphs(scene_text)
        if not paragraphs:
            raise ValueError("narrative canon query requires non-empty scene prose")

        catalog = build_entity_catalog(
            player_name=player_name,
            npc_profiles=npc_profiles,
        )
        schema = schema_json(NarrativeCanonQueryDraftSet)
        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: NarrativeCanonQueryDraftSetLoose) -> None:
            record, retry_errors = _collect_record(output)
            attempt_records[current_attempt] = record
            if retry_errors:
                raise ValueError("; ".join(retry_errors))

        try:
            result = cast(
                NarrativeCanonQueryDraftSetLoose,
                self.llm.run(
                    task="narrative_canon_query",
                    input_data={
                        "numbered_scene_text": _number_paragraphs(paragraphs),
                        "known_entity_catalog_json": json.dumps(
                            [item.model_dump(mode="json") for item in catalog],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "output_schema_json": schema,
                    },
                    output_schema=NarrativeCanonQueryDraftSetLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    temperature=0.2,
                    trace_id=trace_id,
                ),
            )
            if current_attempt in attempt_records:
                record = attempt_records[current_attempt]
            else:
                # StubGateway compatibility.
                record, _ = _collect_record(result)
            self.last_warnings = list(record.warnings)
            return _build_result(record)
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
            LLMProviderError,
        ) as exc:
            return self._salvage(attempt_records, exc)

    def _salvage(
        self,
        attempt_records: dict[int, _AttemptRecord],
        exception: Exception,
    ) -> NarrativeCanonQuerySet:
        latest = attempt_records.get(2)
        if latest is None:
            latest = attempt_records.get(1)
        usable = attempt_records.get(2)
        if usable is None or not usable.queries:
            first = attempt_records.get(1)
            if first is not None and first.queries:
                usable = first
        if usable is None:
            usable = attempt_records.get(2) or attempt_records.get(1)
        if usable is not None and (usable.queries or usable.thought_process):
            warnings = list(usable.warnings)
            if latest is not None and latest is not usable:
                for warning in latest.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
            self.last_warnings = warnings
            return _build_result(usable)

        warnings: list[CanonQueryWarning] = []
        for record in attempt_records.values():
            for warning in record.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        warnings.append(CanonQueryWarning(
            code="canon_query_fallback",
            message=(
                "Canon query generation failed; no queries were generated: "
                f"{exception}"
            ),
        ))
        self.last_warnings = warnings
        return NarrativeCanonQuerySet(
            thought_process="无可用查询",
            queries=[],
        )


def build_entity_catalog(
    *,
    player_name: str,
    npc_profiles: list[NPCProfileDirectorView],
) -> list[CanonEntityReference]:
    references = [
        CanonEntityReference(name=player_name.strip(), kind="player")
    ]
    references.extend(
        CanonEntityReference(
            name=profile.name.strip(),
            aliases=_clean_aliases(profile.aliases, profile.name),
            kind="npc",
        )
        for profile in npc_profiles
        if profile.name.strip()
    )

    deduplicated: list[CanonEntityReference] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        key = (reference.kind, reference.name.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(reference)
    return deduplicated


def _clean_aliases(aliases: list[str], name: str) -> list[str]:
    clean: list[str] = []
    seen = {name.strip().casefold()}
    for alias in aliases:
        value = alias.strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        clean.append(value)
    return clean


def _split_paragraphs(scene_text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", scene_text.strip())]
    return [paragraph for paragraph in paragraphs if paragraph]


def _number_paragraphs(paragraphs: list[str]) -> str:
    return "\n\n".join(
        f"[P{index:03d}]\n{paragraph}"
        for index, paragraph in enumerate(paragraphs, start=1)
    )
