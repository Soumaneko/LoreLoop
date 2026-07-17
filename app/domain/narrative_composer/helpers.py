from app.domain.action.schemas import Anchor
from app.domain.plot_outline.schemas import PlotOutlineResult


def build_entity_retrieval_anchors(outline: PlotOutlineResult) -> list[Anchor]:
    entity_names: list[str] = []
    seen: set[str] = set()
    for event in outline.planned_events:
        for name in event.involved_entities:
            stripped = name.strip()
            key = stripped.casefold()
            if stripped and key not in seen:
                entity_names.append(stripped)
                seen.add(key)

    return [
        Anchor(
            text=name,
            normalized=name,
            kind="character",
            source="explicit",
            confidence=0.8,
        )
        for name in entity_names
    ]
