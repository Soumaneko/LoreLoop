import json
import re
from dataclasses import dataclass
from typing import cast

from pydantic import model_validator

from app.core.llm import LLMGateway
from app.domain.canon_evidence.schemas import CanonEvidenceSet
from app.domain.canon_evidence.views import project_canon_evidence_for_check
from app.domain.current_state.schemas import CurrentState
from app.domain.narrative_canon_query.schemas import NarrativeCanonQuerySet
from app.domain.narrative_composer.schemas import NarrativeComposerResult
from app.domain.views import project_current_state

from .schemas import NarrativeConsistencyReview


SEGMENT_MIN_CHARS = 500
SEGMENT_TARGET_MAX_CHARS = 900
SEGMENT_TAIL_MIN_CHARS = 300
SEGMENT_ABSOLUTE_MAX_CHARS = 1200
ALLOWED_AUTHORITY_REFS = {
    "Player Input",
    "TurnResolutionResult",
    "CurrentState",
    "Historical Canon Evidence",
    "Scene Text",
}


@dataclass(frozen=True)
class _SceneSegment:
    segment_id: str
    start_index: int
    end_index: int
    paragraphs: tuple[str, ...]

    @property
    def paragraph_ids(self) -> list[str]:
        return [
            f"P{index + 1:03d}"
            for index in range(self.start_index, self.end_index + 1)
        ]


class NarrativeConsistencyAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def check(
        self,
        *,
        user_input: str,
        current_state: CurrentState,
        turn_resolution_json: str,
        composition: NarrativeComposerResult,
        canon_query_set: NarrativeCanonQuerySet | None = None,
        canon_evidence_set: CanonEvidenceSet | None = None,
        trace_id: str | None = None,
    ) -> NarrativeConsistencyReview:
        paragraphs = _split_paragraphs(composition.scene_text)
        if not paragraphs:
            raise ValueError("narrative consistency check requires non-empty scene prose")
        segments = _segment_paragraphs(paragraphs)
        all_paragraph_ids = {
            paragraph_id
            for segment in segments
            for paragraph_id in segment.paragraph_ids
        }
        available_queries = {
            query.query_id: query
            for query in (canon_query_set.queries if canon_query_set else [])
        }
        available_evidence = {
            result.query_id: {item.evidence_id for item in result.evidence}
            for result in (canon_evidence_set.results if canon_evidence_set else [])
            if result.evidence
        }
        available_evidence_ids = set().union(*available_evidence.values()) if available_evidence else set()

        class RuntimeNarrativeConsistencyReview(NarrativeConsistencyReview):
            @model_validator(mode="after")
            def _validate_assertion_references(self):
                finding_query_ids = [
                    finding.query_id for finding in self.canon_findings
                ]
                if len(finding_query_ids) != len(set(finding_query_ids)):
                    raise ValueError("canon findings contain duplicate query IDs")
                if set(finding_query_ids) != set(available_evidence):
                    raise ValueError(
                        "canon findings must cover every projected query exactly once"
                    )
                for finding in self.canon_findings:
                    if not set(finding.evidence_ids).issubset(available_evidence_ids):
                        raise ValueError("canon finding cites unavailable evidence")

                for assertion in self.assertions:
                    if not set(assertion.paragraph_ids).issubset(all_paragraph_ids):
                        raise ValueError("assertion cites an unknown paragraph")
                    unknown_refs = (
                        set(assertion.authority_refs) - ALLOWED_AUTHORITY_REFS
                    )
                    if unknown_refs:
                        raise ValueError(
                            f"assertion cites unknown authority refs: {sorted(unknown_refs)}"
                        )
                    if assertion.issue_type != "canon_contradiction":
                        continue
                    if (
                        assertion.canon_query_id not in available_queries
                        or assertion.canon_query_id not in available_evidence
                    ):
                        raise ValueError("canon assertion cites an unavailable query")
                    if not set(assertion.evidence_ids).issubset(available_evidence_ids):
                        raise ValueError("canon assertion cites unavailable evidence")

                contradiction_query_ids = {
                    finding.query_id
                    for finding in self.canon_findings
                    if finding.verdict == "contradiction"
                }
                assertion_query_ids = {
                    assertion.canon_query_id
                    for assertion in self.assertions
                    if assertion.issue_type == "canon_contradiction"
                }
                if assertion_query_ids != contradiction_query_ids:
                    raise ValueError(
                        "canon assertions must match contradiction findings exactly"
                    )
                return self

        review = self.llm.run(
            task="narrative_consistency_check",
            input_data={
                "user_input": user_input,
                "current_state_json": project_current_state(current_state),
                "turn_resolution_json": turn_resolution_json,
                "historical_canon_evidence_json": (
                    project_canon_evidence_for_check(
                        canon_query_set,
                        canon_evidence_set,
                    )
                ),
                "segmented_scene_text": _render_segments(segments),
                "output_schema_json": json.dumps(
                    RuntimeNarrativeConsistencyReview.model_json_schema(),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            output_schema=RuntimeNarrativeConsistencyReview,
            temperature=0.2,
            trace_id=trace_id,
        )
        return cast(RuntimeNarrativeConsistencyReview, review)


def _split_paragraphs(scene_text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", scene_text.strip())]
    return [paragraph for paragraph in paragraphs if paragraph]


def _number_paragraphs(paragraphs: list[str], *, start_index: int = 0) -> str:
    return "\n\n".join(
        f"[P{index + 1:03d}]\n{paragraph}"
        for index, paragraph in enumerate(paragraphs, start=start_index)
    )


def _paragraphs_char_count(paragraphs: list[str] | tuple[str, ...]) -> int:
    return sum(len(paragraph) for paragraph in paragraphs) + max(0, len(paragraphs) - 1) * 2


def _segment_paragraphs(paragraphs: list[str]) -> list[_SceneSegment]:
    groups: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []

    for index, paragraph in enumerate(paragraphs):
        candidate = [text for _, text in current] + [paragraph]
        if (
            current
            and _paragraphs_char_count([text for _, text in current]) >= SEGMENT_MIN_CHARS
            and _paragraphs_char_count(candidate) > SEGMENT_TARGET_MAX_CHARS
        ):
            groups.append(current)
            current = []
        current.append((index, paragraph))
    if current:
        groups.append(current)

    if len(groups) >= 2:
        previous = groups[-2]
        tail = groups[-1]
        while (
            _paragraphs_char_count([text for _, text in tail]) < SEGMENT_TAIL_MIN_CHARS
            and len(previous) > 1
        ):
            candidate_previous = previous[:-1]
            if _paragraphs_char_count(
                [text for _, text in candidate_previous]
            ) < SEGMENT_MIN_CHARS:
                break
            tail.insert(0, previous.pop())

        combined = [*previous, *tail]
        if (
            _paragraphs_char_count([text for _, text in tail]) < SEGMENT_TAIL_MIN_CHARS
            and _paragraphs_char_count([text for _, text in combined])
            <= SEGMENT_ABSOLUTE_MAX_CHARS
        ):
            groups[-2:] = [combined]

    return [
        _SceneSegment(
            segment_id=f"S{segment_index:03d}",
            start_index=group[0][0],
            end_index=group[-1][0],
            paragraphs=tuple(text for _, text in group),
        )
        for segment_index, group in enumerate(groups, start=1)
    ]


def _render_segments(segments: list[_SceneSegment]) -> str:
    rendered: list[str] = []
    for segment in segments:
        paragraph_range = (
            segment.paragraph_ids[0]
            if len(segment.paragraph_ids) == 1
            else f"{segment.paragraph_ids[0]}-{segment.paragraph_ids[-1]}"
        )
        rendered.append(
            f"## {segment.segment_id} | {paragraph_range}\n"
            + _number_paragraphs(
                list(segment.paragraphs),
                start_index=segment.start_index,
            )
        )
    return "\n\n".join(rendered)
