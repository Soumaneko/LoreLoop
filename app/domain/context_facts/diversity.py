from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Generic, Literal, Protocol, TypeVar

from .schemas import ContextSearchIntent, ContextSearchPlan


@dataclass(frozen=True)
class HistoryDiversityPolicy:
    """Deterministic selection and payload bounds for history retrieval."""

    max_hits: int = 10
    max_payload_chars: int = 12_000
    intent_target: int = 2
    entity_cap: int = 3
    turn_cap: int = 2
    max_evidence_text_chars: int = 2_400
    min_excerpt_chars: int = 160
    continuity_item_cap: int = 3

    def __post_init__(self) -> None:
        positive = {
            "max_hits": self.max_hits,
            "max_payload_chars": self.max_payload_chars,
            "intent_target": self.intent_target,
            "entity_cap": self.entity_cap,
            "turn_cap": self.turn_cap,
            "max_evidence_text_chars": self.max_evidence_text_chars,
            "min_excerpt_chars": self.min_excerpt_chars,
            "continuity_item_cap": self.continuity_item_cap,
        }
        invalid = [name for name, value in positive.items() if value < 1]
        if invalid:
            raise ValueError(f"diversity policy values must be positive: {invalid}")
        if self.min_excerpt_chars > self.max_evidence_text_chars:
            raise ValueError(
                "min_excerpt_chars must not exceed max_evidence_text_chars"
            )


class DiversityCandidate(Protocol):
    matched_intent_ids: set[str]
    relation_intent_ids: set[str]
    intent_matched_entities: dict[str, set[str]]
    linked_continuity_ids: set[str]
    document: object


CandidateT = TypeVar("CandidateT", bound=DiversityCandidate)


@dataclass(frozen=True)
class SelectionProposal(Generic[CandidateT]):
    """One route by which a candidate can own a final selection seat."""

    candidate: CandidateT
    owner_intent_id: str
    route: Literal["direct", "relation"]
    channel_rank: int
    signal_rank: int


@dataclass(frozen=True)
class AdmissionPreview:
    """Exact payload state produced if a candidate is selected."""

    state: Any
    payload_chars: int


PreviewCandidate = Callable[[CandidateT], AdmissionPreview | None]
CommitPreview = Callable[[AdmissionPreview], None]


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

_ROUTE_PRIORITY = {
    "direct": 0,
    "relation": 1,
}


def select_diverse_candidates(
    search_plan: ContextSearchPlan,
    *,
    proposals: list[SelectionProposal[CandidateT]],
    policy: HistoryDiversityPolicy,
    preview: PreviewCandidate[CandidateT],
    commit: CommitPreview,
) -> list[CandidateT]:
    """Select one shared pool without comparing route-specific raw scores.

    Every iteration previews the exact globally de-duplicated payload for each
    eligible document. Consequently serialized marginal cost is comparable
    across routes, and a candidate rejected under an earlier payload state is
    naturally retried after any later selection shrinks that payload.
    """

    intents = {intent.intent_id: intent for intent in search_plan.intents}
    intent_order = {
        intent.intent_id: index
        for index, intent in enumerate(search_plan.intents)
    }
    selected: list[CandidateT] = []
    selected_ids: set[str] = set()
    intent_coverage: dict[str, int] = defaultdict(int)
    route_coverage: dict[tuple[str, str], int] = defaultdict(int)
    entity_owner_counts: dict[str, int] = defaultdict(int)
    continuity_item_counts: dict[str, int] = defaultdict(int)
    turn_counts: dict[int, int] = defaultdict(int)
    current_payload_chars = 2  # Compact projection of an empty evidence set.

    def document_id(candidate: CandidateT) -> str:
        return str(getattr(candidate.document, "document_id"))

    def turn_index(candidate: CandidateT) -> int:
        return int(getattr(candidate.document, "turn_index"))

    def owner_supported(
        intent: ContextSearchIntent,
        candidate: CandidateT,
        route: str,
    ) -> bool:
        if route == "relation" or intent.focus != "relationship":
            return True
        return len(
            candidate.intent_matched_entities.get(intent.intent_id, set())
        ) >= 2

    def owner_entity(
        intent: ContextSearchIntent,
        candidate: CandidateT,
    ) -> str | None:
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

    def selectable_intent_ids(candidate: CandidateT) -> set[str]:
        direct_ids = {
            intent_id
            for intent_id in candidate.matched_intent_ids
            if (
                intent_id in intents
                and owner_supported(intents[intent_id], candidate, "direct")
            )
        }
        return direct_ids.union(
            intent_id
            for intent_id in candidate.relation_intent_ids
            if intent_id in intents
        )

    def eligible(proposal: SelectionProposal[CandidateT]) -> bool:
        candidate = proposal.candidate
        candidate_id = document_id(candidate)
        owner = intents.get(proposal.owner_intent_id)
        if (
            owner is None
            or candidate_id in selected_ids
            or turn_counts[turn_index(candidate)] >= policy.turn_cap
            or not owner_supported(owner, candidate, proposal.route)
        ):
            return False
        if proposal.route == "direct":
            entity = owner_entity(owner, candidate)
            return (
                entity is None
                or entity_owner_counts[entity] < policy.entity_cap
            )
        return bool(candidate.linked_continuity_ids) and all(
            continuity_item_counts[item_id] < policy.continuity_item_cap
            for item_id in candidate.linked_continuity_ids
        )

    while len(selected) < policy.max_hits:
        previews: dict[str, AdmissionPreview | None] = {}
        choices: list[
            tuple[tuple, SelectionProposal[CandidateT], AdmissionPreview]
        ] = []
        for proposal in proposals:
            if not eligible(proposal):
                continue
            candidate = proposal.candidate
            candidate_id = document_id(candidate)
            if candidate_id not in previews:
                previews[candidate_id] = preview(candidate)
            candidate_preview = previews[candidate_id]
            if candidate_preview is None:
                continue

            owner = intents[proposal.owner_intent_id]
            coverage = intent_coverage[owner.intent_id]
            covered_ids = selectable_intent_ids(candidate)
            new_coverage = sum(
                intent_coverage[intent_id] < policy.intent_target
                for intent_id in covered_ids
            )
            route_is_new = (
                route_coverage[(owner.intent_id, proposal.route)] == 0
            )
            marginal_chars = (
                candidate_preview.payload_chars - current_payload_chars
            )
            marginal_cost_band = marginal_chars // 128
            key = (
                _ORIGIN_PRIORITY[owner.origin],
                coverage >= policy.intent_target,
                coverage,
                _FOCUS_PRIORITY[owner.focus],
                -new_coverage,
                proposal.signal_rank,
                not route_is_new,
                marginal_cost_band,
                proposal.channel_rank,
                intent_order[owner.intent_id],
                -turn_index(candidate),
                marginal_chars,
                _ROUTE_PRIORITY[proposal.route],
                candidate_id,
            )
            choices.append((key, proposal, candidate_preview))

        if not choices:
            break

        _, chosen, chosen_preview = min(choices, key=lambda choice: choice[0])
        candidate = chosen.candidate
        owner = intents[chosen.owner_intent_id]
        commit(chosen_preview)
        current_payload_chars = chosen_preview.payload_chars
        selected.append(candidate)
        selected_ids.add(document_id(candidate))
        turn_counts[turn_index(candidate)] += 1

        for intent_id in selectable_intent_ids(candidate):
            intent_coverage[intent_id] += 1
        for intent_id in candidate.matched_intent_ids:
            if (
                intent_id in intents
                and owner_supported(intents[intent_id], candidate, "direct")
            ):
                route_coverage[(intent_id, "direct")] += 1
        for intent_id in candidate.relation_intent_ids:
            if intent_id in intents:
                route_coverage[(intent_id, "relation")] += 1

        if chosen.route == "direct":
            entity = owner_entity(owner, candidate)
            if entity is not None:
                entity_owner_counts[entity] += 1
        else:
            for item_id in candidate.linked_continuity_ids:
                continuity_item_counts[item_id] += 1

    return selected
