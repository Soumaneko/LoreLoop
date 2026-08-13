from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.continuity.schemas import (
    ContinuityHistoryBridgeResult,
    ContinuityHistoryLink,
    ContinuityItem,
    ContinuityRelationReason,
)
from app.domain.continuity.store import ContinuityStore

from .schemas import ContextSearchIntent, ContextSearchPlan


_ORIGIN_PRIORITY = {
    "explicit_input": 0,
    "matched_anchor": 1,
    "current_context": 2,
    "relationship": 3,
}

_REASON_PRIORITY: dict[ContinuityRelationReason, int] = {
    "two_entity_match": 0,
    "exact_entity_match": 1,
    "alias_match": 2,
    "subject_topic_match": 3,
    "premise_topic_match": 4,
    "current_state_topic_match": 5,
    "location_match": 6,
}

_STRONG_REASONS: set[ContinuityRelationReason] = {
    "two_entity_match",
    "exact_entity_match",
    "alias_match",
    "subject_topic_match",
    "premise_topic_match",
    "current_state_topic_match",
}


@dataclass
class _ItemMatch:
    item: ContinuityItem
    ranking_intent_reasons: dict[
        str, set[ContinuityRelationReason]
    ] = field(default_factory=dict)
    eligibility_intent_reasons: dict[
        str, set[ContinuityRelationReason]
    ] = field(default_factory=dict)
    matched_entities: set[str] = field(default_factory=set)

    @property
    def triggering_intent_ids(self) -> set[str]:
        return set(self.ranking_intent_reasons)

    @property
    def reasons(self) -> set[ContinuityRelationReason]:
        return set().union(*self.ranking_intent_reasons.values())

    @property
    def eligibility_reasons(self) -> set[ContinuityRelationReason]:
        if not self.eligibility_intent_reasons:
            return set()
        return set().union(*self.eligibility_intent_reasons.values())


class ContinuityHistoryBridge:
    """Resolve SearchPlan structure into trusted History evidence pointers."""

    def __init__(
        self,
        store: ContinuityStore,
        *,
        active_limit: int = 6,
        archived_limit: int = 4,
    ):
        if active_limit < 1 or archived_limit < 1:
            raise ValueError("continuity bridge limits must be positive")
        self.store = store
        self.active_limit = active_limit
        self.archived_limit = archived_limit

    def retrieve(
        self,
        search_plan: ContextSearchPlan,
    ) -> ContinuityHistoryBridgeResult:
        intent_order = {
            intent.intent_id: index
            for index, intent in enumerate(search_plan.intents)
        }
        intents = {
            intent.intent_id: intent
            for intent in search_plan.intents
        }
        matches = [
            match
            for item in self.store.list_all()
            if (match := _match_item(item, search_plan.intents)) is not None
            and _eligible(match, intents)
        ]
        matches.sort(key=lambda match: _match_key(
            match,
            intents=intents,
            intent_order=intent_order,
        ))

        selected: list[_ItemMatch] = []
        active_count = 0
        archived_count = 0
        for match in matches:
            if match.item.status == "active":
                if active_count >= self.active_limit:
                    continue
                active_count += 1
            else:
                if archived_count >= self.archived_limit:
                    continue
                archived_count += 1
            selected.append(match)

        # The lifecycle skeleton is selected in SQL and all evidence for those
        # nodes is loaded in one batch. Total update count does not affect the
        # number of queries or the number of lifecycle nodes materialized.
        logs_by_item: dict[str, list] = {
            match.item.id: [] for match in selected
        }
        for log in self.store.bridge_lifecycle_logs(
            match.item.id for match in selected
        ):
            logs_by_item[log.item_id].append(log)

        links: list[ContinuityHistoryLink] = []
        for match in selected:
            triggering_intent_ids = sorted(
                match.triggering_intent_ids,
                key=lambda intent_id: (intent_order[intent_id], intent_id),
            )
            reasons = _sorted_reasons(match.reasons)
            intent_match_reasons = {
                intent_id: _sorted_reasons(ranking_reasons)
                for intent_id, ranking_reasons in sorted(
                    match.ranking_intent_reasons.items()
                )
            }
            eligibility_reasons = _sorted_reasons(
                match.eligibility_reasons
            )
            for log in logs_by_item[match.item.id]:
                basis_primary, basis_context, basis_complete = (
                    _evidence_basis(match.item, log)
                )
                for evidence in log.evidence:
                    links.append(ContinuityHistoryLink(
                        continuity_id=match.item.id,
                        continuity_status=match.item.status,
                        operation=log.operation,
                        evidence_id=evidence.evidence_id,
                        evidence_hash=evidence.evidence_hash,
                        triggering_intent_ids=triggering_intent_ids,
                        match_reasons=reasons,
                        intent_match_reasons=intent_match_reasons,
                        eligibility_reasons=eligibility_reasons,
                        evidence_position=evidence.position,
                        evidence_basis_primary=basis_primary,
                        evidence_basis_context=basis_context,
                        basis_complete=basis_complete,
                    ))

        return ContinuityHistoryBridgeResult(
            links=links,
            matched_continuity_ids=[match.item.id for match in selected],
            matched_active_continuities=sum(
                match.item.status == "active" for match in selected
            ),
            matched_archived_continuities=sum(
                match.item.status != "active" for match in selected
            ),
        )


def _evidence_basis(
    item: ContinuityItem,
    log,
) -> tuple[str | None, str | None, bool]:
    """Derive the node-specific semantic basis from persisted lifecycle data.

    ``create`` uses the immutable premise as its primary basis; ``update`` uses
    the per-operation state delta plus reason; resolve/cancel use the closing
    reason. The full current_state is deliberately excluded: it is the latest
    aggregated snapshot and would back-fill later information into older nodes.
    """

    def _join(parts: list[str | None]) -> str | None:
        cleaned = [part.strip() for part in parts if part and part.strip()]
        return "\n".join(cleaned) if cleaned else None

    if log.operation == "create":
        primary = _join([item.premise])
        # The current subject is excluded: it is mutable metadata that may have
        # been rewritten after creation and would back-fill a later title onto
        # the create node. Only the immutable premise acts as its basis.
        context = None
        complete = not log.migration_created and primary is not None
    elif log.operation == "update":
        primary = _join([log.state_delta, log.reason])
        context = _join([item.subject, item.premise])
        complete = (
            not log.migration_created
            and bool(log.state_delta and log.state_delta.strip())
        )
    else:
        primary = _join([log.reason])
        context = None
        complete = not log.migration_created and primary is not None
    return primary, context, complete


def _match_item(
    item: ContinuityItem,
    intents: list[ContextSearchIntent],
) -> _ItemMatch | None:
    match = _ItemMatch(item=item)
    item_entities = {
        entity.strip().casefold(): entity
        for entity in item.entities
        if entity.strip()
    }
    item_location = (item.location or "").strip().casefold()
    subject = item.subject.strip().casefold()
    premise = item.premise.strip().casefold()
    current_state = (item.current_state or "").strip().casefold()

    for intent in intents:
        ranking_reasons: set[ContinuityRelationReason] = set()
        eligibility_reasons: set[ContinuityRelationReason] = set()
        for entity in intent.entities:
            surfaces = {
                surface.strip().casefold()
                for surface in [entity.name, *entity.aliases]
                if surface.strip()
            }
            if intent.focus != "location":
                name = entity.name.strip().casefold()
                if name and name in item_entities:
                    ranking_reasons.add("exact_entity_match")
                    eligibility_reasons.add("exact_entity_match")
                    match.matched_entities.add(item_entities[name])
                for alias in entity.aliases:
                    alias_key = alias.strip().casefold()
                    if alias_key and alias_key in item_entities:
                        ranking_reasons.add("alias_match")
                        eligibility_reasons.add("alias_match")
                        match.matched_entities.add(item_entities[alias_key])
            if item_location and item_location in surfaces:
                ranking_reasons.add("location_match")

        for topic in intent.topics:
            topic_key = topic.strip().casefold()
            if not topic_key:
                continue
            if topic_key in item_entities:
                ranking_reasons.add("exact_entity_match")
                eligibility_reasons.add("exact_entity_match")
                match.matched_entities.add(item_entities[topic_key])

            # Location is a ranking-only signal. An identical location string
            # repeated in the prose fields cannot become an archived trigger.
            if item_location and topic_key == item_location:
                ranking_reasons.add("location_match")
                continue
            if intent.focus == "location":
                continue

            if _contains_either(subject, topic_key):
                ranking_reasons.add("subject_topic_match")
                eligibility_reasons.add("subject_topic_match")
            if _contains_either(premise, topic_key):
                ranking_reasons.add("premise_topic_match")
                eligibility_reasons.add("premise_topic_match")
            if _contains_either(current_state, topic_key):
                ranking_reasons.add("current_state_topic_match")
                eligibility_reasons.add("current_state_topic_match")

        if ranking_reasons:
            match.ranking_intent_reasons[intent.intent_id] = ranking_reasons
        if eligibility_reasons:
            match.eligibility_intent_reasons[
                intent.intent_id
            ] = eligibility_reasons

    if len(match.matched_entities) >= 2:
        for reasons in match.ranking_intent_reasons.values():
            reasons.add("two_entity_match")
        for reasons in match.eligibility_intent_reasons.values():
            reasons.add("two_entity_match")
    return match if match.ranking_intent_reasons else None


def _eligible(
    match: _ItemMatch,
    intents: dict[str, ContextSearchIntent],
) -> bool:
    if match.item.status == "active":
        return True
    if len(match.matched_entities) >= 2:
        return True
    return any(
        intents[intent_id].origin in {"explicit_input", "matched_anchor"}
        and bool(reasons & _STRONG_REASONS)
        for intent_id, reasons in match.eligibility_intent_reasons.items()
    )


def _match_key(
    match: _ItemMatch,
    *,
    intents: dict[str, ContextSearchIntent],
    intent_order: dict[str, int],
) -> tuple:
    best_origin = min(
        _ORIGIN_PRIORITY[intents[intent_id].origin]
        for intent_id in match.triggering_intent_ids
    )
    best_reason = min(_REASON_PRIORITY[reason] for reason in match.reasons)
    first_intent = min(
        intent_order[intent_id]
        for intent_id in match.triggering_intent_ids
    )
    return (
        best_origin,
        best_reason,
        -len(match.matched_entities),
        -("location_match" in match.reasons),
        match.item.status != "active",
        -match.item.updated_turn,
        first_intent,
        match.item.id,
    )


def _sorted_reasons(
    reasons: set[ContinuityRelationReason],
) -> list[ContinuityRelationReason]:
    return sorted(reasons, key=lambda reason: (
        _REASON_PRIORITY[reason],
        reason,
    ))


def _contains_either(left: str, right: str) -> bool:
    return bool(left and right and (left in right or right in left))
