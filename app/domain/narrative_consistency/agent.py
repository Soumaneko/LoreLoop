import json
import math
import re
from dataclasses import dataclass
from typing import cast

from pydantic import model_validator

from app.core.llm import LLMGateway
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.context_facts.views import project_context_facts
from app.domain.current_state.schemas import CurrentState
from app.domain.narrative_composer.schemas import NarrativeComposerResult
from app.domain.plot_outline.views import project_turn_resolution
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.turn_resolution.schemas import TurnResolutionResult
from app.domain.views import project_current_state

from .schemas import NarrativeConsistencyOutcome, NarrativeConsistencyReview


SEGMENT_MIN_CHARS = 500
SEGMENT_TARGET_MAX_CHARS = 900
SEGMENT_TAIL_MIN_CHARS = 300
SEGMENT_ABSOLUTE_MAX_CHARS = 1200


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


class NarrativeConsistencyRejectedError(RuntimeError):
    """The semantic contract itself is unsafe to repair as a prose-only patch."""


class NarrativeConsistencyAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def check(
        self,
        *,
        user_input: str,
        current_state: CurrentState,
        turn_resolution: TurnResolutionResult,
        context_facts: ContextFactSet,
        recent_scene: ShortTermScene | None,
        composition: NarrativeComposerResult,
        trace_id: str | None = None,
    ) -> NarrativeConsistencyOutcome:
        paragraphs = _split_paragraphs(composition.scene_text)
        if not paragraphs:
            raise ValueError("narrative consistency check requires non-empty scene prose")
        segments = _segment_paragraphs(paragraphs)
        expected_segment_ids = [segment.segment_id for segment in segments]
        expected_paragraph_ids = {
            segment.segment_id: segment.paragraph_ids
            for segment in segments
        }
        all_paragraph_ids = {
            paragraph_id
            for segment in segments
            for paragraph_id in segment.paragraph_ids
        }

        class RuntimeNarrativeConsistencyReview(NarrativeConsistencyReview):
            @model_validator(mode="after")
            def _validate_complete_segment_coverage(self):
                actual_segment_ids = [
                    segment.segment_id
                    for segment in self.segment_reviews
                ]
                if actual_segment_ids != expected_segment_ids:
                    raise ValueError(
                        "segment reviews must cover every segment exactly once and in order; "
                        f"expected {expected_segment_ids}, got {actual_segment_ids}"
                    )
                for segment in self.segment_reviews:
                    expected_ids = expected_paragraph_ids[segment.segment_id]
                    if segment.paragraph_ids != expected_ids:
                        raise ValueError(
                            f"{segment.segment_id} must review paragraphs {expected_ids}, "
                            f"got {segment.paragraph_ids}"
                        )
                    for issue in segment.issues:
                        if not set(issue.paragraph_ids).issubset(expected_ids):
                            raise ValueError(
                                f"{segment.segment_id} issue cites paragraphs outside its segment"
                            )
                for issue in self.cross_segment_review.issues:
                    if not set(issue.paragraph_ids).issubset(all_paragraph_ids):
                        raise ValueError("cross-segment issue cites an unknown paragraph")
                return self

        review = cast(
            RuntimeNarrativeConsistencyReview,
            self.llm.run(
                task="narrative_consistency_check",
                input_data={
                    "user_input": user_input,
                    "current_state_json": project_current_state(current_state),
                    "turn_resolution_json": project_turn_resolution(turn_resolution),
                    "context_facts_json": project_context_facts(context_facts),
                    "recent_scene_json": _project_recent_scene(recent_scene),
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
            ),
        )

        if review.decision == "reject":
            raise NarrativeConsistencyRejectedError(review.summary)
        if review.decision == "pass":
            return NarrativeConsistencyOutcome(
                review=review,
                scene_text=composition.scene_text,
            )

        scene_text, changed_ids = _apply_patches(paragraphs, review)
        return NarrativeConsistencyOutcome(
            review=review,
            scene_text=scene_text,
            changed_paragraph_ids=changed_ids,
        )


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


def _paragraph_index(paragraph_id: str, paragraph_count: int) -> int:
    index = int(paragraph_id[1:]) - 1
    if index < 0 or index >= paragraph_count:
        raise ValueError(f"paragraph id out of range: {paragraph_id}")
    return index


def _apply_patches(
    paragraphs: list[str],
    review: NarrativeConsistencyReview,
) -> tuple[str, list[str]]:
    paragraph_count = len(paragraphs)
    ranges: list[tuple[int, int, str]] = []
    changed_ids: list[str] = []
    issue_paragraph_ids = {
        paragraph_id
        for issue in review.all_issues
        for paragraph_id in issue.paragraph_ids
    }

    for patch in review.patches:
        start = _paragraph_index(patch.start_paragraph_id, paragraph_count)
        end = _paragraph_index(patch.end_paragraph_id, paragraph_count)
        if start > end:
            raise ValueError(
                f"patch range is reversed: {patch.start_paragraph_id}-{patch.end_paragraph_id}"
            )
        patch_ids = {
            f"P{index + 1:03d}"
            for index in range(start, end + 1)
        }
        if not patch_ids.issubset(issue_paragraph_ids):
            raise ValueError("paragraph patch is not supported by a reported issue")
        ranges.append((start, end, patch.replacement.strip()))

    ranges.sort(key=lambda item: item[0])
    previous_end = -1
    affected_count = 0
    for start, end, replacement in ranges:
        if start <= previous_end:
            raise ValueError("paragraph patches overlap")
        previous_end = end
        affected_count += end - start + 1
        source = "\n\n".join(paragraphs[start:end + 1])
        max_replacement_length = max(len(source) + 160, math.ceil(len(source) * 1.5))
        if len(replacement) > max_replacement_length:
            raise ValueError("paragraph patch expands the source beyond the safety limit")
        changed_ids.extend(f"P{index + 1:03d}" for index in range(start, end + 1))

    max_affected = max(1, min(4, math.ceil(paragraph_count * 0.2)))
    if affected_count > max_affected:
        raise ValueError(
            "narrative consistency patch affects too much prose; regeneration is required"
        )

    result = list(paragraphs)
    for start, end, replacement in reversed(ranges):
        result[start:end + 1] = [replacement] if replacement else []

    scene_text = "\n\n".join(result).strip()
    if not scene_text:
        raise ValueError("narrative consistency patches removed the entire scene")
    return scene_text, changed_ids


def _project_recent_scene(scene: ShortTermScene | None) -> str:
    if scene is None:
        return "null"
    return json.dumps(
        {
            "scene_id": scene.scene_id,
            "turn_index": scene.turn_index,
            "scene_text": scene.scene_text,
        },
        ensure_ascii=False,
        indent=2,
    )
