from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from app.domain.confirmed_history import (
    HistoryDocument,
    history_document_text_hash,
)
from app.domain.continuity import ContinuityHistoryLink
from app.domain.continuity.schemas import ContinuityOperationType

from .evidence_support import (
    CLASS_RANK,
    classify_link_support,
    link_window_text,
)


@dataclass(frozen=True)
class RelationAdmission:
    """Already-judged relation semantics for one History document.

    Produced by :func:`judge_relation_links`; consumed by the retriever and the
    diversity selector without re-deriving node->evidence validity, per-intent
    attribution, merge semantics, or degraded status. A document served by
    multiple lifecycle links carries the per-intent quality/class/degraded
    verdict those links jointly imply.
    """

    intent_quality: dict[str, float] = field(default_factory=dict)
    intent_class: dict[str, str] = field(default_factory=dict)
    intent_degraded: dict[str, bool] = field(default_factory=dict)
    matched_continuity_ids: frozenset[str] = field(default_factory=frozenset)
    linked_operations: frozenset[ContinuityOperationType] = field(
        default_factory=frozenset
    )
    match_reasons: frozenset[str] = field(default_factory=frozenset)
    intent_first_order: dict[str, int] = field(default_factory=dict)

    @property
    def served_intent_ids(self) -> set[str]:
        return set(self.intent_quality)

    @property
    def evidence_quality(self) -> float:
        return max(self.intent_quality.values(), default=0.0)

    @property
    def evidence_class(self) -> str | None:
        """Document-level class: the intent with the best quality, class
        rank breaking equal-quality ties across intents."""
        qualities = self.intent_quality
        if not qualities:
            return None
        best_intent = max(
            qualities,
            key=lambda intent_id: (
                qualities[intent_id],
                CLASS_RANK.get(self.intent_class.get(intent_id, ""), -1),
            ),
        )
        return self.intent_class.get(best_intent)

    @property
    def signal_rank(self) -> int:
        """Categorical structural-link tier for final selection."""
        reasons = self.match_reasons
        if reasons.intersection({
            "two_entity_match",
            "subject_topic_match",
            "premise_topic_match",
            "current_state_topic_match",
        }):
            return 0
        if reasons.intersection({"exact_entity_match", "alias_match"}):
            return 2
        if "location_match" in reasons:
            return 4
        return 6

    def degraded_for(self, intent_id: str) -> bool:
        return self.intent_degraded.get(intent_id, False)

    @classmethod
    def merge(
        cls,
        admissions: Sequence["RelationAdmission | None"],
    ) -> "RelationAdmission":
        """Merge a cluster's admissions onto its representative.

        Per-intent quality is the max across members; the class tie-break only
        consults members whose quality equals that max; degraded is the AND
        across members that actually serve the intent, so a direct-only member
        (``None``) never un-degrades a relation attribution. Per-intent first
        order is the min across members that carry it; remaining fields union.
        """
        serving_members = [admission for admission in admissions if admission]
        if not serving_members:
            return cls()
        intent_ids = set().union(
            *(set(admission.intent_quality) for admission in serving_members)
        )

        intent_quality = {
            intent_id: max(
                admission.intent_quality.get(intent_id, 0.0)
                for admission in serving_members
            )
            for intent_id in intent_ids
        }
        intent_class: dict[str, str] = {}
        for intent_id, best in intent_quality.items():
            best_class: str | None = None
            best_rank = -1
            for admission in serving_members:
                member_quality = admission.intent_quality.get(intent_id)
                member_class = admission.intent_class.get(intent_id)
                if (
                    member_quality is None
                    or member_class is None
                    or member_quality != best
                ):
                    continue
                rank = CLASS_RANK.get(member_class, -1)
                if rank > best_rank:
                    best_rank = rank
                    best_class = member_class
            if best_class is not None:
                intent_class[intent_id] = best_class
        intent_degraded = {
            intent_id: all(
                admission.intent_degraded.get(intent_id, True)
                for admission in serving_members
                if intent_id in admission.intent_quality
            )
            for intent_id in intent_ids
        }
        intent_first_order: dict[str, int] = {}
        for intent_id in intent_ids:
            orders = [
                admission.intent_first_order[intent_id]
                for admission in serving_members
                if intent_id in admission.intent_first_order
            ]
            if orders:
                intent_first_order[intent_id] = min(orders)

        return cls(
            intent_quality=intent_quality,
            intent_class=intent_class,
            intent_degraded=intent_degraded,
            matched_continuity_ids=frozenset().union(*(
                admission.matched_continuity_ids
                for admission in serving_members
            )),
            linked_operations=frozenset().union(*(
                admission.linked_operations for admission in serving_members
            )),
            match_reasons=frozenset().union(*(
                admission.match_reasons for admission in serving_members
            )),
            intent_first_order=intent_first_order,
        )


@dataclass(frozen=True)
class RelationLinkDiagnostics:
    """Per-link funnel counts for the relation admission pass."""

    valid_links: int = 0
    stale_links: int = 0
    invalid_links: int = 0
    degraded_links: int = 0


class _AdmissionAccumulator:
    """Build one frozen RelationAdmission from the links that touch a document."""

    __slots__ = (
        "_intent_quality",
        "_intent_class",
        "_intent_degraded",
        "_intent_first_order",
        "_continuity_ids",
        "_operations",
        "_reasons",
    )

    def __init__(self) -> None:
        self._intent_quality: dict[str, float] = {}
        self._intent_class: dict[str, str] = {}
        self._intent_degraded: dict[str, bool] = {}
        self._intent_first_order: dict[str, int] = {}
        self._continuity_ids: set[str] = set()
        self._operations: set[ContinuityOperationType] = set()
        self._reasons: set[str] = set()

    def absorb(self, link: ContinuityHistoryLink, support, order: int) -> None:
        for intent_id in link.triggering_intent_ids:
            current = self._intent_quality.get(intent_id)
            if (
                current is None
                or support.quality > current
                or (
                    support.quality == current
                    and CLASS_RANK.get(support.support_class, -1)
                    > CLASS_RANK.get(
                        self._intent_class.get(intent_id, ""),
                        -1,
                    )
                )
            ):
                self._intent_quality[intent_id] = support.quality
                self._intent_class[intent_id] = support.support_class
            self._intent_degraded[intent_id] = (
                self._intent_degraded.get(intent_id, True) and support.degraded
            )
            current_order = self._intent_first_order.get(intent_id)
            if current_order is None or order < current_order:
                self._intent_first_order[intent_id] = order
        self._continuity_ids.add(link.continuity_id)
        self._operations.add(link.operation)
        self._reasons.update(link.match_reasons)

    def admission(self) -> RelationAdmission:
        return RelationAdmission(
            intent_quality=dict(self._intent_quality),
            intent_class=dict(self._intent_class),
            intent_degraded=dict(self._intent_degraded),
            matched_continuity_ids=frozenset(self._continuity_ids),
            linked_operations=frozenset(self._operations),
            match_reasons=frozenset(self._reasons),
            intent_first_order=dict(self._intent_first_order),
        )


def judge_relation_links(
    links: list[ContinuityHistoryLink],
    *,
    documents_by_id: dict[str, HistoryDocument],
    by_turn: dict[int, list[HistoryDocument]],
) -> tuple[dict[str, RelationAdmission], RelationLinkDiagnostics]:
    """Resolve, validate, and judge every lifecycle link in one pass.

    Returns the per-document judged admissions (only documents that cleared
    validation and attribution survive) plus the diagnostic funnel. A link
    whose complete basis scores zero support loses its attribution
    unconditionally; incomplete bases fail open.
    """
    nodes: dict[tuple[str, str], list[ContinuityHistoryLink]] = defaultdict(list)
    for link in links:
        nodes[(link.continuity_id, link.operation)].append(link)
    node_evidence_ids = {
        node_key: {link.evidence_id for link in node_links}
        for node_key, node_links in nodes.items()
    }

    admissions: dict[str, RelationAdmission] = {}
    accumulators: dict[str, _AdmissionAccumulator] = {}
    diagnostics = RelationLinkDiagnostics()
    for order, link in enumerate(links):
        document = documents_by_id.get(link.evidence_id)
        if (
            document is None
            or document.source_type != "scene_paragraph"
            or (
                link.evidence_hash is not None
                and history_document_text_hash(document) != link.evidence_hash
            )
        ):
            diagnostics = replace(
                diagnostics,
                stale_links=diagnostics.stale_links + 1,
            )
            continue
        diagnostics = replace(
            diagnostics,
            valid_links=diagnostics.valid_links + 1,
        )

        window_text = link_window_text(
            document,
            by_turn[document.turn_index],
            node_evidence_ids[(link.continuity_id, link.operation)],
        )
        support = classify_link_support(
            basis_primary=link.evidence_basis_primary,
            basis_context=link.evidence_basis_context,
            basis_complete=link.basis_complete,
            window_text=window_text,
        )
        if _suppress_attribution(link, support):
            diagnostics = replace(
                diagnostics,
                invalid_links=diagnostics.invalid_links + 1,
            )
            continue
        if support.degraded:
            diagnostics = replace(
                diagnostics,
                degraded_links=diagnostics.degraded_links + 1,
            )

        accumulator = accumulators.get(document.document_id)
        if accumulator is None:
            accumulator = _AdmissionAccumulator()
            accumulators[document.document_id] = accumulator
        accumulator.absorb(link, support, order)

    for document_id, accumulator in accumulators.items():
        admissions[document_id] = accumulator.admission()
    return admissions, diagnostics


def _suppress_attribution(
    link: ContinuityHistoryLink,
    support,
) -> bool:
    """Strip a relation attribution that has no support at all.

    A link with a complete basis whose evidence scores zero support is an
    error mount and never claims the matter, even when it is the node's only
    evidence. Incomplete bases fail open; every other class keeps its
    attribution.
    """
    return support.basis_complete and support.support_class == "zero"


def degraded_rescue_allowed(
    owner_intent_id: str,
    direct_covered_intents: set[str],
) -> bool:
    """A degraded relation proposal may only rescue an intent that has no
    direct hit yet, judged by its own owner intent (never another intent's
    coverage)."""
    return owner_intent_id not in direct_covered_intents


def partition_served(
    direct_intents: set[str],
    relation_intents: set[str],
    degraded_relation_intents: set[str],
) -> tuple[set[str], set[str]]:
    """Split a selected document's served intents into normal vs degraded.

    Normal coverage is any direct intent plus any non-degraded relation
    intent. A degraded relation intent that is also directly served counts as
    normal coverage and is not a rescue.
    """
    normal = direct_intents | (relation_intents - degraded_relation_intents)
    degraded = degraded_relation_intents - normal
    return normal, degraded
