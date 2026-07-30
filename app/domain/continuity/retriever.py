from __future__ import annotations

import re

from app.domain.action.schemas import Anchor
from app.domain.current_state import CurrentState

from .schemas import ContinuityMatch, ContinuitySelection
from .store import ContinuityStore


class ContinuityRetriever:
    def __init__(self, store: ContinuityStore):
        self.store = store

    def retrieve(
        self,
        *,
        user_input: str,
        current_state: CurrentState,
        anchors: list[Anchor],
        limit: int = 8,
    ) -> ContinuitySelection:
        active_items = self.store.list_active()
        text = user_input.casefold()
        anchor_terms = {
            term.strip().casefold()
            for anchor in anchors
            for term in (anchor.text, anchor.normalized)
            if term.strip()
        }
        participant_terms = {
            name.strip().casefold()
            for name in current_state.scene.participant_names
            if name.strip()
        }
        location = current_state.scene.location.strip().casefold()
        lexical_terms = _lexical_terms(user_input)
        fts_ids = self.store.fts_search([
            *anchor_terms,
            *participant_terms,
            *lexical_terms,
            location,
        ])

        matches: list[ContinuityMatch] = []
        for item in active_items:
            matched_entities = sorted({
                entity
                for entity in item.entities
                if (
                    entity.casefold() in text
                    or entity.casefold() in anchor_terms
                    or entity.casefold() in participant_terms
                )
            })
            matched_location = bool(
                item.location
                and item.location.strip().casefold() == location
            )
            matched_deadline = bool(
                item.deadline
                and (
                    item.deadline.casefold() in text
                    or item.deadline
                    == current_state.world_clock.label
                )
            )
            has_fts = item.id in fts_ids
            if not (
                matched_entities
                or matched_location
                or matched_deadline
                or has_fts
            ):
                continue
            score = (
                0.36 * min(2, len(matched_entities))
                + (0.24 if matched_location else 0.0)
                + (0.30 if matched_deadline else 0.0)
                + (0.18 if has_fts else 0.0)
            )
            matches.append(ContinuityMatch(
                item=item,
                matched_entities=matched_entities,
                matched_location=matched_location,
                matched_deadline=matched_deadline,
                score=round(score, 6),
            ))

        matches.sort(
            key=lambda match: (
                match.score,
                match.item.updated_turn,
            ),
            reverse=True,
        )
        return ContinuitySelection(
            items=matches[:limit],
            active_count=len(active_items),
        )


def _lexical_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for segment in re.findall(
        r"[A-Za-z0-9_\u4e00-\u9fff]{3,}",
        text,
    ):
        clean = segment.casefold()
        if len(clean) <= 8:
            terms.add(clean)
            continue
        terms.update(
            clean[index:index + 4]
            for index in range(0, len(clean) - 2, 2)
        )
    return terms
