from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Generic, Literal, Protocol, TypeVar

from .relation_admission import RelationAdmission
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
    relation_owner_cap: int = 3

    def __post_init__(self) -> None:
        positive = {
            "max_hits": self.max_hits,
            "max_payload_chars": self.max_payload_chars,
            "intent_target": self.intent_target,
            "entity_cap": self.entity_cap,
            "turn_cap": self.turn_cap,
            "max_evidence_text_chars": self.max_evidence_text_chars,
            "min_excerpt_chars": self.min_excerpt_chars,
            "relation_owner_cap": self.relation_owner_cap,
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
    intent_matched_entities: dict[str, set[str]]
    relation_admission: RelationAdmission | None
    # Only document_id/turn_index are duck-typed off the opaque document.
    document: Any


CandidateT = TypeVar("CandidateT", bound=DiversityCandidate)


@dataclass(frozen=True)
class SelectionProposal(Generic[CandidateT]):
    """One route by which a candidate can own a final selection seat."""

    candidate: CandidateT
    owner_intent_id: str
    route: Literal["direct", "relation"]
    channel_rank: int
    signal_rank: int
    #: Cross-intent recency tie-break that sorts before payload cost. Pure NPC
    #: field links set this to ``current_turn_index - turn_index`` (0 = current,
    #: newer first, never negative) so a recent node wins over an older one
    #: across intents without outranking the direct route; Continuity, mixed and
    #: direct proposals keep 0. Relation channel ordering additionally blends the
    #: bounded turn-distance bonus into ``channel_rank`` (issue 05).
    recency_rank: int = 0
    #: For relation proposals: the exact evidence document whose attribution
    #: this proposal carries (source-scoped, pre-cluster). ``None`` for direct.
    source_evidence_id: str | None = None
    #: Source-scoped semantic verdicts for relation proposals. These are
    #: carried directly on the proposal and must be read instead of the merged
    #: ``candidate.relation_admission``: a source-specific weak link must never
    #: borrow another cluster member's strong quality, and a deleted source's
    #: reasons must never reach the output. Direct proposals keep defaults.
    scoped_quality: float = 0.0
    scoped_class: str | None = None
    scoped_degraded: bool = False
    scoped_match_reasons: frozenset[str] = frozenset()
    #: For relation proposals: scoped attribution keys served by this
    #: proposal's intent (NPC change tuples / continuity ids / owner keys).
    #: Empty for direct proposals.
    scoped_owner_keys: frozenset[str] = frozenset()
    scoped_npc_changes: frozenset[tuple[str, str, int]] = frozenset()
    scoped_continuity_ids: frozenset[str] = frozenset()
    scoped_operations: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AcceptedEntry(Generic[CandidateT]):
    """One document admitted through a proposal that owns a new seat."""

    proposal: SelectionProposal[CandidateT]
    candidate: CandidateT


@dataclass(frozen=True)
class AcceptedAugmentation(Generic[CandidateT]):
    """One accepted zero-cost relation augmentation on an already-selected doc.

    Keeps the exact source evidence and scoped attribution so the retriever can
    merge provenance only for proposals that actually won, and can drop a slice
    whose source evidence no longer survives the final payload (no ghost
    provenance).
    """

    proposal: SelectionProposal[CandidateT]
    candidate: CandidateT
    source_evidence_id: str


@dataclass(frozen=True)
class SelectionResult(Generic[CandidateT]):
    """Full selection decision: what entered, how, and what augmented.

    The selector never mutates history payloads directly; the retriever
    materializes final hits from these events so the generic selector stays
    free of History schema dependencies.
    """

    selected: list[CandidateT]
    entries: list[AcceptedEntry[CandidateT]]
    augmentations: list[AcceptedAugmentation[CandidateT]]


@dataclass(frozen=True)
class AdmissionPreview:
    """Exact payload state produced if a candidate is selected.

    ``visible_evidence_ids`` exposes which evidence documents survive in that
    payload (main plus supporting). The selector uses it to reject relation
    proposals whose source evidence would be stripped by budget compression
    *before* bookkeeping — a ghost proposal must never consume coverage or
    owner budget.
    """

    state: Any
    payload_chars: int
    visible_evidence_ids: frozenset[str] = frozenset()


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
) -> SelectionResult[CandidateT]:
    """Select one shared pool without comparing route-specific raw scores.

    Every iteration previews the exact globally de-duplicated payload for each
    eligible document. Consequently serialized marginal cost is comparable
    across routes, and a candidate rejected under an earlier payload state is
    naturally retried after any later selection shrinks that payload.

    Returns a :class:`SelectionResult`: the selected documents plus the exact
    entry/augmentation proposals that won them, so the caller can materialize
    provenance without the selector depending on a History schema.
    """

    intents = {intent.intent_id: intent for intent in search_plan.intents}
    intent_order = {
        intent.intent_id: index
        for index, intent in enumerate(search_plan.intents)
    }
    selected: list[CandidateT] = []
    selected_ids: set[str] = set()
    entries: list[AcceptedEntry[CandidateT]] = []
    augmentations: list[AcceptedAugmentation[CandidateT]] = []
    accepted_augmentation_slices: set[tuple[str, str, str]] = set()
    accepted_account_keys: set[tuple[str, str, str]] = set()
    committed_visible_evidence_ids: frozenset[str] = frozenset()
    intent_coverage: dict[str, int] = defaultdict(int)
    route_coverage: dict[tuple[str, str], int] = defaultdict(int)
    entity_owner_counts: dict[str, int] = defaultdict(int)
    relation_owner_counts: dict[str, int] = defaultdict(int)
    turn_counts: dict[int, int] = defaultdict(int)
    current_payload_chars = 2  # Compact projection of an empty evidence set.

    def document_id(candidate: CandidateT) -> str:
        return candidate.document.document_id

    def turn_index(candidate: CandidateT) -> int:
        return candidate.document.turn_index

    def degraded(proposal: SelectionProposal[CandidateT]) -> bool:
        """Source-scoped degraded status.

        Reads the proposal's own verdict, never the merged admission, so a
        strong link on one cluster member never promotes a weak proposal on
        another member of the same document.
        """
        return proposal.scoped_degraded

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

    def _augmentation_key(
        proposal: SelectionProposal[CandidateT],
    ) -> tuple:
        """Deterministic augmentation processing order.

        Uses the same public layers as main selection (origin, remaining
        coverage, focus) before route-local node-evidence support quality and
        deterministic tie-breaks.  There is no owner-budget competition among
        augmentations, so this key only makes the output deterministic.
        """
        owner = intents.get(proposal.owner_intent_id)
        if owner is None:
            origin = 9
            focus = 9
            coverage = 0
        else:
            origin = _ORIGIN_PRIORITY[owner.origin]
            focus = _FOCUS_PRIORITY[owner.focus]
            coverage = intent_coverage[owner.intent_id]
        return (
            origin,
            coverage >= policy.intent_target,
            coverage,
            focus,
            -proposal.scoped_quality,
            proposal.channel_rank,
            proposal.signal_rank,
            proposal.recency_rank,
            intent_order.get(proposal.owner_intent_id, 1_000_000_000),
            proposal.candidate.document.document_id,
        )

    def relation_owner_eligible(
        proposal: SelectionProposal[CandidateT],
        owner: ContextSearchIntent,
    ) -> bool:
        """Relation-specific caps: only the source-scoped owner cap applies.

        Zero-support proposals never reach here (judged earlier). Degraded
        (weak) evidence competes like any other proposal — issue 17 removed the
        direct-coverage veto and the old rescue rate-limit, so a weak relation
        is limited only by its own source's owner budget and final selection,
        never by another cluster member's caps or a per-intent rescue quota.
        """
        if not proposal.scoped_owner_keys:
            return False
        return all(
            relation_owner_counts[owner_key] < policy.relation_owner_cap
            for owner_key in proposal.scoped_owner_keys
        )

    def eligible(proposal: SelectionProposal[CandidateT]) -> bool:
        """Ordinary admission: the proposal may own a brand-new seat.

        A document already selected is not eligible here — later relation
        proposals for it go through the augmentation pass instead (see the
        post-loop ``augmentations`` phase), so one document still owns at most
        one main seat.
        """
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
        return relation_owner_eligible(proposal, owner)

    def _account_proposal(
        proposal: SelectionProposal[CandidateT],
        *,
        owner: ContextSearchIntent,
        direct_served: set[str],
    ) -> None:
        """Proposal-scoped bookkeeping for the accepted selection.

        A direct proposal is only the entry ticket: the document itself can
        lexically serve several intents at once, so every directly matched
        intent is credited (one document, many direct intents). A relation
        proposal credits only its own intent — never the whole admission — so
        B's win can never mark C as covered ("B wins, C rides along").
        """
        if proposal.route == "direct":
            for intent_id in direct_served:
                intent_coverage[intent_id] += 1
                route_coverage[(intent_id, "direct")] += 1
            return
        intent_id = proposal.owner_intent_id
        intent_coverage[intent_id] += 1
        route_coverage[(intent_id, "relation")] += 1

    def run_augmentations() -> None:
        """Accept zero-cost relation augmentations for selected documents.

        Runs after every ordinary admission so coverage updates before the
        next seat competition. Each accepted augmentation adds provenance only
        — never a seat, payload copy, or duplicate document. The relation
        owner cap does not apply here: a selected document already occupies a
        main hit, and augmentation only adds attribution for other intents.
        Dedup is per ``(document, intent, source_evidence_id)`` — two cluster
        members serving the same intent may both augment (distinct slices);
        coverage is accounted once per ``(document, intent, route)``.
        """
        augmentable = [
            proposal
            for proposal in proposals
            if (
                proposal.route == "relation"
                and proposal.source_evidence_id is not None
                and proposal.source_evidence_id in committed_visible_evidence_ids
                and proposal.candidate.document.document_id in selected_ids
            )
        ]
        for proposal in sorted(augmentable, key=_augmentation_key):
            candidate = proposal.candidate
            document_key = document_id(candidate)
            source_evidence_id = proposal.source_evidence_id
            if source_evidence_id is None:
                continue
            owner = intents.get(proposal.owner_intent_id)
            if owner is None or proposal.scoped_class == "zero":
                continue
            # Only the ghost guard (source evidence survives) and dedup apply;
            # no owner-budget decision is made for augmentations.
            slice_key = (
                document_key,
                proposal.owner_intent_id,
                source_evidence_id,
            )
            if slice_key in accepted_augmentation_slices:
                continue
            augmentation = AcceptedAugmentation(
                proposal=proposal,
                candidate=candidate,
                source_evidence_id=source_evidence_id,
            )
            accepted_augmentation_slices.add(slice_key)
            account_key = (document_key, proposal.owner_intent_id, "relation")
            if account_key not in accepted_account_keys:
                accepted_account_keys.add(account_key)
                _account_proposal(
                    proposal,
                    owner=owner,
                    direct_served=set(),
                )
            augmentations.append(augmentation)

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
            # Ghost proposal guard: a relation proposal whose source evidence
            # would be stripped from the final payload must not participate in
            # selection or bookkeeping at all — it can never own a seat, spend
            # owner budget, or advance coverage.
            if (
                proposal.route == "relation"
                and proposal.source_evidence_id is not None
                and proposal.source_evidence_id
                not in candidate_preview.visible_evidence_ids
            ):
                continue

            owner = intents[proposal.owner_intent_id]
            coverage = intent_coverage[owner.intent_id]
            if proposal.route == "direct":
                # A direct document may lexically serve several intents at
                # once; the coverage gain it advertises is the number of its
                # directly matched intents still under target (matches what is
                # actually credited after selection).
                new_coverage = sum(
                    1
                    for intent_id in candidate.matched_intent_ids
                    if intent_id in intents
                    and owner_supported(intents[intent_id], candidate, "direct")
                    and intent_coverage[intent_id] < policy.intent_target
                )
            else:
                # Relation proposals credit only their own intent. Weak
                # quality is expressed through source-scoped channel_rank, not
                # by faking a zero coverage gain.
                new_coverage = (
                    1
                    if intent_coverage[proposal.owner_intent_id]
                    < policy.intent_target
                    else 0
                )
            route_is_new = (
                route_coverage[(owner.intent_id, proposal.route)] == 0
            )
            marginal_chars = (
                candidate_preview.payload_chars - current_payload_chars
            )
            marginal_cost_band = marginal_chars // 128
            # Shared key shape for every route.  Element 6 is the quality
            # discriminator: for relation proposals it is channel_rank (the
            # per-intent node-evidence support order already blended with the
            # bounded turn-distance bonus), so within a triggered unit quality
            # precedes trigger tier and payload cost.  Direct proposals keep a
            # constant 0 there so their categorical tier (signal_rank) stays the
            # deciding feature and the SPEC direct-tier semantics are untouched.
            quality_tie_rank = (
                0
                if proposal.route == "direct"
                else proposal.channel_rank
            )
            key = (
                _ORIGIN_PRIORITY[owner.origin],
                coverage >= policy.intent_target,
                coverage,
                _FOCUS_PRIORITY[owner.focus],
                -new_coverage,
                quality_tie_rank,
                proposal.signal_rank,
                proposal.recency_rank,
                not route_is_new,
                marginal_cost_band,
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
        committed_visible_evidence_ids = chosen_preview.visible_evidence_ids
        selected.append(candidate)
        selected_ids.add(document_id(candidate))
        turn_counts[turn_index(candidate)] += 1

        entries.append(AcceptedEntry(proposal=chosen, candidate=candidate))
        account_key = (
            document_id(candidate),
            chosen.owner_intent_id,
            chosen.route,
        )
        if account_key not in accepted_account_keys:
            accepted_account_keys.add(account_key)
            _account_proposal(
                chosen,
                owner=owner,
                direct_served=(
                    {
                        intent_id
                        for intent_id in candidate.matched_intent_ids
                        if intent_id in intents
                        and owner_supported(
                            intents[intent_id], candidate, "direct"
                        )
                    }
                    if chosen.route == "direct"
                    else set()
                ),
            )
        if chosen.route == "relation":
            # A relation entry already owns this (document, intent) seat; the
            # augmentation pass must not re-accept the same slice.
            if chosen.source_evidence_id is not None:
                accepted_augmentation_slices.add((
                    document_id(candidate),
                    chosen.owner_intent_id,
                    chosen.source_evidence_id,
                ))

        if chosen.route == "direct":
            entity = owner_entity(owner, candidate)
            if entity is not None:
                entity_owner_counts[entity] += 1
        else:
            # Source-scoped owner budget: only this proposal's own intent's
            # owner keys are consumed (never the merged admission).
            for owner_key in chosen.scoped_owner_keys:
                relation_owner_counts[owner_key] += 1

        # Zero-cost augmentation after every ordinary admission: already
        # selected documents may accept relation proposals for other intents
        # immediately, so coverage is updated before the next seat competition
        # instead of flooding extra documents first.
        run_augmentations()

    return SelectionResult(
        selected=selected,
        entries=entries,
        augmentations=augmentations,
    )
