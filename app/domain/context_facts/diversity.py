from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

from .schemas import ContextSearchIntent, ContextSearchPlan


@dataclass(frozen=True)
class HistoryDiversityPolicy:
    """Deterministic quotas and payload bounds for history retrieval."""

    max_hits: int = 10
    max_payload_chars: int = 12_000
    intent_target: int = 2
    entity_cap: int = 3
    turn_cap: int = 2
    max_evidence_text_chars: int = 2_400
    min_excerpt_chars: int = 160
    relation_hit_limit: int = 3

    def __post_init__(self) -> None:
        positive = {
            "max_hits": self.max_hits,
            "max_payload_chars": self.max_payload_chars,
            "intent_target": self.intent_target,
            "entity_cap": self.entity_cap,
            "turn_cap": self.turn_cap,
            "max_evidence_text_chars": self.max_evidence_text_chars,
            "min_excerpt_chars": self.min_excerpt_chars,
        }
        invalid = [name for name, value in positive.items() if value < 1]
        if invalid:
            raise ValueError(f"diversity policy values must be positive: {invalid}")
        if self.min_excerpt_chars > self.max_evidence_text_chars:
            raise ValueError(
                "min_excerpt_chars must not exceed max_evidence_text_chars"
            )
        if self.relation_hit_limit < 0:
            raise ValueError("relation_hit_limit must be non-negative")


class DiversityCandidate(Protocol):
    score: float
    matched_intent_ids: set[str]
    intent_scores: dict[str, float]
    intent_matched_entities: dict[str, set[str]]
    document: object


CandidateT = TypeVar("CandidateT", bound=DiversityCandidate)
AdmitCandidate = Callable[[CandidateT, str], tuple[bool, bool]]


_FOCUS_PRIORITY = {
    "entity": 0,
    "topic": 0,
    "location": 1,
    "relationship": 2,
}

_ORIGIN_PRIORITY = {
    "explicit_input": 0,
    "matched_anchor": 1,
    "current_context": 2,
    "relationship": 3,
}


def select_diverse_candidates(
    search_plan: ContextSearchPlan,
    *,
    by_intent: dict[str, list[CandidateT]],
    candidates: dict[str, CandidateT],
    policy: HistoryDiversityPolicy,
    admit: AdmitCandidate[CandidateT],
    supplements_by_origin: dict[
        str,
        list[tuple[CandidateT, str]],
    ] | None = None,
) -> list[CandidateT]:
    """Select candidates with quota coverage first, then global score fill.

    ``admit`` owns payload materialization and returns ``(accepted, shrank)``.
    Counters are only changed after acceptance, so quota accounting always
    matches the final payload; a later shrink permits one deterministic retry.
    Entity caps apply to the entity intent chosen as the selection owner, not to
    every entity mentioned by a document.
    """
    intents = {intent.intent_id: intent for intent in search_plan.intents}
    intent_order = {
        intent.intent_id: index
        for index, intent in enumerate(search_plan.intents)
    }
    selected: list[CandidateT] = []
    selected_ids: set[str] = set()
    intent_coverage: dict[str, int] = defaultdict(int)
    entity_owner_counts: dict[str, int] = defaultdict(int)
    turn_counts: dict[int, int] = defaultdict(int)
    supplement_count = 0
    supplements_by_origin = supplements_by_origin or {}

    def document_id(candidate: CandidateT) -> str:
        return str(getattr(candidate.document, "document_id"))

    def turn_index(candidate: CandidateT) -> int:
        return int(getattr(candidate.document, "turn_index"))

    def owner_entity(intent: ContextSearchIntent, candidate: CandidateT) -> str | None:
        if intent.focus != "entity" or not intent.entities:
            return None
        matched = {
            str(value).casefold()
            for value in candidate.intent_matched_entities.get(
                intent.intent_id,
                set(),
            )
        }
        for entity in intent.entities:
            if entity.name.casefold() in matched:
                return entity.name.casefold()
        return intent.entities[0].name.casefold()

    def owner_supported(intent: ContextSearchIntent, candidate: CandidateT) -> bool:
        if intent.focus != "relationship":
            return True
        return len(candidate.intent_matched_entities.get(intent.intent_id, set())) >= 2

    def owner_available(intent: ContextSearchIntent, candidate: CandidateT) -> bool:
        if not owner_supported(intent, candidate):
            return False
        entity = owner_entity(intent, candidate)
        return entity is None or entity_owner_counts[entity] < policy.entity_cap

    def covered_intent_ids(candidate: CandidateT) -> set[str]:
        return {
            intent_id
            for intent_id in candidate.matched_intent_ids
            if (
                intent_id in intents
                and owner_supported(intents[intent_id], candidate)
            )
        }

    def accept(
        candidate: CandidateT,
        owner: ContextSearchIntent,
    ) -> tuple[bool, bool]:
        candidate_id = document_id(candidate)
        turn = turn_index(candidate)
        if (
            candidate_id in selected_ids
            or len(selected) >= policy.max_hits
            or turn_counts[turn] >= policy.turn_cap
            or not owner_available(owner, candidate)
        ):
            return False, False
        admitted, payload_shrank = admit(candidate, owner.intent_id)
        if not admitted:
            return False, False
        selected.append(candidate)
        selected_ids.add(candidate_id)
        turn_counts[turn] += 1
        entity = owner_entity(owner, candidate)
        if entity is not None:
            entity_owner_counts[entity] += 1
        for intent_id in covered_intent_ids(candidate):
            intent_coverage[intent_id] += 1
        return True, payload_shrank

    def tier_owner_ids(candidate: CandidateT, origin_priority: int) -> list[str]:
        return sorted(
            (
                intent_id
                for intent_id in covered_intent_ids(candidate)
                if (
                    _ORIGIN_PRIORITY[intents[intent_id].origin]
                    == origin_priority
                )
            ),
            key=lambda intent_id: (
                -candidate.intent_scores.get(intent_id, 0.0),
                _FOCUS_PRIORITY[intents[intent_id].focus],
                intent_order[intent_id],
                intent_id,
            ),
        )

    def fill_origin_tier(origin_priority: int) -> None:
        remaining = sorted(
            candidates.values(),
            key=lambda candidate: (
                -max(
                    (
                        candidate.intent_scores.get(intent_id, 0.0)
                        for intent_id in tier_owner_ids(
                            candidate,
                            origin_priority,
                        )
                    ),
                    default=0.0,
                ),
                -turn_index(candidate),
                document_id(candidate),
            ),
        )
        retry_after_shrink = True
        while retry_after_shrink and len(selected) < policy.max_hits:
            retry_after_shrink = False
            prior_failure = False
            for candidate in remaining:
                if len(selected) >= policy.max_hits:
                    break
                if (
                    document_id(candidate) in selected_ids
                    or turn_counts[turn_index(candidate)] >= policy.turn_cap
                ):
                    continue
                owner_ids = tier_owner_ids(candidate, origin_priority)
                if not owner_ids:
                    continue
                accepted = False
                for owner_id in owner_ids:
                    accepted, payload_shrank = accept(
                        candidate,
                        intents[owner_id],
                    )
                    if accepted:
                        if payload_shrank and prior_failure:
                            retry_after_shrink = True
                        break
                if not accepted:
                    prior_failure = True

    def accept_supplements(origin: str) -> None:
        nonlocal supplement_count
        for candidate, owner_id in supplements_by_origin.get(origin, []):
            if (
                supplement_count >= policy.relation_hit_limit
                or len(selected) >= policy.max_hits
            ):
                return
            candidate_id = document_id(candidate)
            turn = turn_index(candidate)
            owner = intents.get(owner_id)
            if (
                owner is None
                or owner.origin != origin
                or candidate_id in selected_ids
                or turn_counts[turn] >= policy.turn_cap
            ):
                continue
            admitted, _payload_shrank = admit(candidate, owner_id)
            if not admitted:
                continue
            selected.append(candidate)
            selected_ids.add(candidate_id)
            turn_counts[turn] += 1
            supplement_count += 1

    origin_priorities = sorted({
        _ORIGIN_PRIORITY[intent.origin]
        for intent in search_plan.intents
    })
    for origin_priority in origin_priorities:
        origin = next(
            intent.origin
            for intent in search_plan.intents
            if _ORIGIN_PRIORITY[intent.origin] == origin_priority
        )
        focus_priorities = sorted({
            _FOCUS_PRIORITY[intent.focus]
            for intent in search_plan.intents
            if _ORIGIN_PRIORITY[intent.origin] == origin_priority
        })
        for focus_priority in focus_priorities:
            priority_intents = [
                intent
                for intent in search_plan.intents
                if (
                    _ORIGIN_PRIORITY[intent.origin] == origin_priority
                    and _FOCUS_PRIORITY[intent.focus] == focus_priority
                )
            ]
            for target in range(1, policy.intent_target + 1):
                for intent in priority_intents:
                    if len(selected) >= policy.max_hits:
                        return selected
                    if intent_coverage[intent.intent_id] >= target:
                        continue
                    eligible = [
                        candidate
                        for candidate in by_intent.get(intent.intent_id, [])
                        if (
                            document_id(candidate) not in selected_ids
                            and turn_counts[turn_index(candidate)] < policy.turn_cap
                            and owner_available(intent, candidate)
                        )
                    ]
                    if not eligible:
                        continue

                    def phase_one_key(candidate: CandidateT):
                        new_coverage = sum(
                            1
                            for intent_id in covered_intent_ids(candidate)
                            if (
                                intent_id in intents
                                and intent_coverage[intent_id]
                                < policy.intent_target
                            )
                        )
                        return (
                            -candidate.intent_scores.get(
                                intent.intent_id,
                                candidate.score,
                            ),
                            -new_coverage,
                            -turn_index(candidate),
                            document_id(candidate),
                        )

                    for candidate in sorted(eligible, key=phase_one_key):
                        accepted, _ = accept(candidate, intent)
                        if accepted:
                            break
        accept_supplements(origin)
        fill_origin_tier(origin_priority)

    return selected
