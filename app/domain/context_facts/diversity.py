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
    #: Recency tie-break that sorts before payload cost. Pure NPC field links
    #: set this to ``current_turn_index - turn_index`` (0 = current, newer
    #: first, never negative) so a recent node wins over an older one without
    #: outranking the direct route; Continuity, mixed and direct proposals keep
    #: 0 so their relative ordering is unchanged.
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
    #: How each selected document entered: "direct" or "relation". Direct-owned
    #: documents never consume relation owner budget (SPEC); relation-owned
    #: documents count against every linked owner, including owners introduced
    #: later by augmentation.
    document_entry_route: dict[str, str] = {}
    document_consumed_owners: dict[str, set[str]] = defaultdict(set)
    #: (document, owner) -> accepted augmentations consuming that owner's
    #: budget on that document. The owner cap counts one (document, owner)
    #: group per slot regardless of how many source slices it carries, so all
    #: slices of one document for one owner are evicted or kept together.
    owner_groups: dict[
        tuple[str, str], list[AcceptedAugmentation[CandidateT]]
    ] = defaultdict(list)
    #: owner -> (document, owner) groups currently occupying its budget via
    #: augmentation.
    augmentation_owner_occupancy: dict[
        str, set[tuple[str, str]]
    ] = defaultdict(set)
    #: Reverse index: accepted augmentation -> the (document, owner) groups it
    #: belongs to. Keyed by the unique (document, intent, source_evidence_id)
    #: slice triple (deduped at registration) instead of the augmentation
    #: object, which may wrap an unhashable candidate. A multi-owner
    #: augmentation appears in several groups; eviction from any one group must
    #: drop it from every group and only then roll back coverage once.
    augmentation_membership: dict[
        tuple[str, str, str], set[tuple[str, str]]
    ] = defaultdict(set)
    #: Membership keys of augmentations that actually credited their intent's
    #: coverage when accepted (the first holder of a (document, intent,
    #: relation) account). Owner-cap competition compares coverage
    #: counterfactually — excluding the competing groups' own credits — so an
    #: incumbent's self-credit can never make it look "covered" and a
    #: challenger "uncovered" in the same comparison.
    augmentation_coverage_credits: set[tuple[str, str, str]] = set()
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

    def _owner_occupied(owner_key: str) -> int:
        """Total owner occupancy: relation-entry seats plus (document, owner)
        augmentation groups consuming this owner's budget."""
        return relation_owner_counts[owner_key] + len(
            augmentation_owner_occupancy[owner_key]
        )

    def _augmentation_priority(
        proposal: SelectionProposal[CandidateT],
        *,
        coverage_of: Callable[[str], int] | None = None,
    ) -> tuple:
        """Shared augmentation comparison key (SPEC §8.1 public layers).

        Mirrors the main selection key's leading layers — intent origin,
        remaining coverage, semantic focus — before the route-local quality
        dimension (node-evidence support quality via ``scoped_quality``, which
        is cross-intent comparable; ``channel_rank`` only resolves
        same-quality ties inside one intent), then deterministic tie-breaks.
        One key drives the initial processing order, the weakest owner-group
        choice, and the replacement/eviction decision, so a lower-priority
        origin can never evict higher-priority explicit-input evidence no
        matter how strong its node evidence is.

        ``coverage_of`` supplies the coverage value used in the comparison.
        Owner-cap competition passes a counterfactual view that excludes the
        competing groups' own coverage credits, so the comparison state is
        symmetric between incumbent and challenger and the outcome depends
        only on the shared priority layers plus quality — never on who
        happened to be accepted first.
        """
        owner = intents.get(proposal.owner_intent_id)
        if owner is None:
            origin = 9
            focus = 9
            coverage = 0
        else:
            origin = _ORIGIN_PRIORITY[owner.origin]
            focus = _FOCUS_PRIORITY[owner.focus]
            coverage = (
                coverage_of(owner.intent_id)
                if coverage_of is not None
                else intent_coverage[owner.intent_id]
            )
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

    def _group_priority(
        group: list[AcceptedAugmentation[CandidateT]],
        *,
        coverage_of: Callable[[str], int] | None = None,
    ) -> tuple:
        """Priority of a (document, owner) group: its best slice (min key).

        Eviction compares a proposal against this, so one strong slice
        protects the whole group and a weak slice can never drag it down.
        ``coverage_of`` is forwarded to every slice's comparison key.
        """
        return min(
            (
                _augmentation_priority(
                    item.proposal,
                    coverage_of=coverage_of,
                )
                for item in group
            ),
            default=(
                9,
                True,
                1 << 30,
                9,
                0.0,
                0,
                0,
                0,
                1_000_000_000,
                "",
            ),
        )

    def _account_still_held(document_key: str, intent_id: str) -> bool:
        """True when a surviving accepted entry or augmentation still provides
        the (document, intent, relation) account, i.e. coverage must stay
        credited.

        Coverage is credited once per (document, intent, route); an
        augmentation being dropped may share that account with a relation entry
        (a cluster member augmenting the entry's own intent) or with another
        surviving augmentation. Only when nothing survives does the rollback
        decrement coverage.
        """
        for entry in entries:
            if (
                entry.candidate.document.document_id == document_key
                and entry.proposal.owner_intent_id == intent_id
                and entry.proposal.route == "relation"
            ):
                return True
        for aug in augmentations:
            if (
                aug.candidate.document.document_id == document_key
                and aug.proposal.owner_intent_id == intent_id
            ):
                return True
        return False

    def _entry_consumes_owner(document_key: str, owner_key: str) -> bool:
        """True when the document's relation entry seat still consumes this
        owner's budget. Such an owner must stay in ``document_consumed_owners``
        even after its last augmentation group empties, so later slices keep
        riding the entry seat instead of being re-checked against the cap."""
        for entry in entries:
            if (
                entry.candidate.document.document_id == document_key
                and entry.proposal.route == "relation"
                and owner_key in entry.proposal.scoped_owner_keys
            ):
                return True
        return False

    def _membership_key(
        augmentation: AcceptedAugmentation[CandidateT],
    ) -> tuple[str, str, str]:
        """Unique slice key: (document, intent, source evidence)."""
        return (
            augmentation.candidate.document.document_id,
            augmentation.proposal.owner_intent_id,
            augmentation.source_evidence_id,
        )

    def _drop_augmentation(
        augmentation: AcceptedAugmentation[CandidateT],
    ) -> None:
        """Fully remove one augmentation: output entry, coverage accounting
        (once, per its unique (doc, intent, route) account, and only while no
        other survivor still holds that account), dedup keys, and every
        (document, owner) membership — a multi-owner augmentation is dropped
        from *all* its groups, and a group that empties frees the owner's
        occupancy (unless the entry seat still consumes that owner)."""
        document_key = augmentation.candidate.document.document_id
        if augmentation in augmentations:
            augmentations.remove(augmentation)
        proposal = augmentation.proposal
        intent_id = proposal.owner_intent_id
        account_key = (document_key, intent_id, "relation")
        if (
            account_key in accepted_account_keys
            and not _account_still_held(document_key, intent_id)
        ):
            accepted_account_keys.discard(account_key)
            intent_coverage[intent_id] = max(
                0, intent_coverage[intent_id] - 1
            )
            route_coverage[(intent_id, "relation")] = max(
                0, route_coverage[(intent_id, "relation")] - 1
            )
        accepted_augmentation_slices.discard((
            document_key,
            intent_id,
            augmentation.source_evidence_id,
        ))
        membership_key = _membership_key(augmentation)
        augmentation_coverage_credits.discard(membership_key)
        for group_key in list(augmentation_membership[membership_key]):
            group_owner = group_key[1]
            group = owner_groups.get(group_key)
            if group is not None:
                # Remove this augmentation from the group; if the group is now
                # empty it no longer occupies the owner's budget.
                remaining = [
                    item for item in group if item is not augmentation
                ]
                if remaining:
                    owner_groups[group_key] = remaining
                else:
                    owner_groups.pop(group_key, None)
                    augmentation_owner_occupancy[group_owner].discard(
                        group_key
                    )
                    if not _entry_consumes_owner(
                        group_key[0], group_owner
                    ):
                        document_consumed_owners[group_key[0]].discard(
                            group_owner
                        )
        augmentation_membership.pop(membership_key, None)

    def _evict_owner_group(
        document_key: str,
        owner_key: str,
    ) -> None:
        """Remove an entire (document, owner) augmentation group: every
        augmentation in it is dropped (from all its memberships, with single
        coverage rollback); the group then frees its owner occupancy (unless a
        relation entry seat still consumes that owner)."""
        group = owner_groups.pop((document_key, owner_key), [])
        for augmentation in list(group):
            _drop_augmentation(augmentation)
        augmentation_owner_occupancy[owner_key].discard(
            (document_key, owner_key)
        )
        if not _entry_consumes_owner(document_key, owner_key):
            document_consumed_owners[document_key].discard(owner_key)

    def _try_consume_augmentation_owners(
        proposal: SelectionProposal[CandidateT],
        candidate: CandidateT,
        new_owners: list[str],
    ) -> bool:
        """Reserve owner budget for a relation-owned augmentation, atomically.

        The owner cap counts one ``(document, owner)`` group per slot: all
        source slices of one document for one owner are evicted or kept
        together. A later stronger proposal may evict an earlier weaker group
        (node-evidence support quality first). Eviction is planned first and
        committed only when every new owner can be satisfied, so a rejected
        proposal never leaves partial state.

        Group registration itself happens exactly once, in
        ``run_augmentations`` — this function only *decides* and performs
        evictions, it never appends the new augmentation.
        """
        document_key = document_id(candidate)
        eviction_plan: dict[str, str] = {}
        for owner_key in new_owners:
            if _owner_occupied(owner_key) < policy.relation_owner_cap:
                continue
            # At cap: only a weaker already-accepted group may be evicted, and
            # only if this proposal is strictly more prioritized under the
            # shared public layers (origin, coverage, focus) then quality —
            # never by raw quality alone (SPEC §8.1). Occupancy may come from
            # a relation entry seat (no augmentation group to evict).
            occupant_groups = augmentation_owner_occupancy[owner_key]
            if not occupant_groups:
                return False

            # Counterfactual coverage: exclude every competing group's own
            # coverage credits, so an incumbent's self-credit never makes it
            # look "covered" against an unchallenged "uncovered" challenger
            # (and vice versa after an eviction rolls back). The comparison
            # state is then symmetric and the outcome depends only on the
            # shared priority layers plus quality — never on acceptance order.
            def competition_coverage(intent_id: str) -> int:
                competing_credits = sum(
                    1
                    for (doc_id, owner) in occupant_groups
                    for aug in owner_groups[(doc_id, owner)]
                    if (
                        aug.proposal.owner_intent_id == intent_id
                        and _membership_key(aug)
                        in augmentation_coverage_credits
                    )
                )
                return max(0, intent_coverage[intent_id] - competing_credits)

            proposal_key = _augmentation_priority(
                proposal, coverage_of=competition_coverage
            )
            evictable = [
                (doc_id, owner)
                for (doc_id, owner) in occupant_groups
                if proposal_key
                < _group_priority(
                    owner_groups[(doc_id, owner)],
                    coverage_of=competition_coverage,
                )
            ]
            if not evictable:
                return False
            # Evict the least-prioritized evictable group (largest key), so a
            # higher-priority group is kept whenever possible.
            weakest = max(
                evictable,
                key=lambda pair: _group_priority(
                    owner_groups[pair],
                    coverage_of=competition_coverage,
                ),
            )
            eviction_plan[owner_key] = weakest[0]
        # Atomic commit: only after every new owner is satisfiable. Re-check
        # each owner's cap because evicting one group may cascade-free another
        # owner (a multi-owner augmentation is dropped from every group at
        # once); in that case the planned weaker group is left untouched.
        for owner_key, evicted_doc in eviction_plan.items():
            if _owner_occupied(owner_key) >= policy.relation_owner_cap:
                _evict_owner_group(evicted_doc, owner_key)
        return True

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
            _owner_occupied(owner_key) < policy.relation_owner_cap
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

        Runs after every ordinary admission so coverage and owner caps update
        before the next seat competition. Each accepted augmentation adds
        provenance only — never a seat, payload copy, or duplicate document.
        Dedup is per ``(document, intent, source_evidence_id)`` — two cluster
        members serving the same intent may both augment (distinct slices);
        coverage/owner accounting is per ``(document, intent, route)`` so the
        same intent is credited once per document.
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
        # Competition among augmentations that need the same owner budget uses
        # the same shared comparison key as ordinary proposals (origin,
        # coverage, focus), then node-evidence support quality: the initial
        # processing order, the weakest owner-group choice, and the eviction
        # decision all use `_augmentation_priority`.
        def augmentation_key(proposal: SelectionProposal[CandidateT]) -> tuple:
            return _augmentation_priority(proposal)

        for proposal in sorted(augmentable, key=augmentation_key):
            candidate = proposal.candidate
            document_key = document_id(candidate)
            source_evidence_id = proposal.source_evidence_id
            if source_evidence_id is None:
                continue
            owner = intents.get(proposal.owner_intent_id)
            if owner is None or proposal.scoped_class == "zero":
                continue
            # Only the ghost guard (source evidence survives) and dedup apply
            # before any owner-budget decision.
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
            # Augmentation attaches provenance to an already-selected document.
            # Direct-owned documents are never blocked by the relation owner
            # cap (SPEC); relation-owned documents count against every linked
            # owner, so a *new* owner introduced by this augmentation must
            # still be within budget. A later stronger augmentation may
            # *replace* an earlier weaker one under the same owner cap
            # (node-evidence support quality first); each (document, owner)
            # pair is consumed at most once — later slices for an already
            # consumed owner join the same group for free.
            entry_route = document_entry_route.get(document_key)
            new_owners = [
                owner_key
                for owner_key in proposal.scoped_owner_keys
                if owner_key not in document_consumed_owners[document_key]
            ]
            if entry_route == "relation" and new_owners:
                accepted = _try_consume_augmentation_owners(
                    proposal, candidate, new_owners
                )
                if not accepted:
                    continue
            accepted_augmentation_slices.add(slice_key)
            account_key = (document_key, proposal.owner_intent_id, "relation")
            membership_key = (
                document_key,
                proposal.owner_intent_id,
                source_evidence_id,
            )
            if account_key not in accepted_account_keys:
                accepted_account_keys.add(account_key)
                _account_proposal(
                    proposal,
                    owner=owner,
                    direct_served=set(),
                )
                # This slice is the first holder of the (document, intent,
                # relation) account: it credits coverage, so owner-cap
                # competition must exclude its self-credit counterfactually.
                augmentation_coverage_credits.add(membership_key)
            augmentations.append(augmentation)
            # Single registration point: every slice joins each of its
            # (document, owner) groups (and the reverse index), so eviction
            # removes all slices of one document for one owner together and a
            # multi-owner augmentation is cleaned from every group at once. A
            # brand-new group occupies the owner's budget slot only for
            # relation-owned documents and only when the document does not
            # already consume that owner (its entry seat or an earlier
            # augmentation group already counts it) — direct-owned documents
            # never consume relation owner budget (SPEC).
            for owner_key in proposal.scoped_owner_keys:
                group_key = (document_key, owner_key)
                is_new_group = group_key not in owner_groups
                owner_groups[group_key].append(augmentation)
                augmentation_membership[membership_key].add(group_key)
                if (
                    entry_route == "relation"
                    and is_new_group
                    and owner_key not in document_consumed_owners[document_key]
                ):
                    augmentation_owner_occupancy[owner_key].add(group_key)
                document_consumed_owners[document_key].add(owner_key)

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
            # per-intent node-evidence support order), so within a triggered
            # unit quality precedes trigger tier, recency and payload cost
            # (issue 16).  Direct proposals keep a constant 0 there so their
            # categorical tier (signal_rank) stays the deciding feature and
            # the SPEC direct-tier semantics are untouched.
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
            document_entry_route[document_id(candidate)] = "direct"
        else:
            # Source-scoped owner budget: only this proposal's own intent's
            # owner keys are consumed (never the merged admission).
            for owner_key in chosen.scoped_owner_keys:
                relation_owner_counts[owner_key] += 1
                document_consumed_owners[document_id(candidate)].add(owner_key)
            document_entry_route[document_id(candidate)] = "relation"

        # Zero-cost augmentation after every ordinary admission: already
        # selected documents may accept relation proposals for other intents
        # immediately, so coverage/owner caps are updated before the next seat
        # competition instead of flooding extra documents first.
        run_augmentations()

    return SelectionResult(
        selected=selected,
        entries=entries,
        augmentations=augmentations,
    )
