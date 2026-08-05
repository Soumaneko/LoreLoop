from __future__ import annotations

from app.domain.context_facts.schemas import (
    ContextSearchPlan,
    SettingMatch,
    SettingSelection,
)
from app.domain.current_state import CurrentState
from app.domain.world_setting import WorldSettingStore


class SettingRetriever:
    """Deterministic retrieval of relevant public World Settings for Context Facts.

    Only publicly known Setting facts are projected here; hidden facts never
    leave the World Setting store through this path.
    """

    def __init__(self, store: WorldSettingStore):
        self.store = store

    def retrieve(
        self,
        *,
        user_input: str,
        current_state: CurrentState,
        search_plan: ContextSearchPlan,
        limit: int = 4,
    ) -> SettingSelection:
        settings = self.store.list_all()
        user_text = user_input.casefold()
        location = current_state.scene.location.strip().casefold()
        participant_terms = {
            name.strip().casefold()
            for name in current_state.scene.participant_names
            if name.strip()
        }
        entity_terms = {
            surface.strip().casefold()
            for intent in search_plan.intents
            for entity in intent.entities
            for surface in [entity.name, *entity.aliases]
            if surface.strip()
        }
        topic_terms = {
            topic.strip().casefold()
            for intent in search_plan.intents
            for topic in intent.topics
            if topic.strip()
        }

        matches: list[SettingMatch] = []
        for setting in settings:
            name = setting.name.strip().casefold()
            public_text = " ".join(
                [setting.name, setting.summary, *setting.public_facts]
            ).casefold()
            matched_location = bool(name == location)
            matched_name_in_input = bool(name and name in user_text)
            matched_entities = sorted({
                term for term in entity_terms
                if term and term in public_text
            })
            matched_topics = sorted({
                term for term in topic_terms
                if term and term in public_text
            })
            matched_participants = sorted({
                term for term in participant_terms
                if term and term in public_text
            })
            if not (
                matched_location
                or matched_name_in_input
                or matched_entities
                or matched_topics
                or matched_participants
            ):
                continue
            score = (
                (0.50 if matched_location else 0.0)
                + (0.40 if matched_name_in_input else 0.0)
                + 0.30 * min(2, len(matched_entities))
                + 0.20 * min(2, len(matched_topics))
                + 0.15 * min(2, len(matched_participants))
            )
            matches.append(SettingMatch(
                setting_id=setting.setting_id,
                kind=setting.kind,
                name=setting.name,
                summary=setting.summary,
                public_facts=list(setting.public_facts),
                matched_location=matched_location,
                matched_name_in_input=matched_name_in_input,
                matched_entities=matched_entities,
                matched_topics=matched_topics,
                score=round(score, 6),
            ))

        matches.sort(
            key=lambda match: (match.score, match.setting_id),
            reverse=True,
        )
        return SettingSelection(
            settings=matches[:limit],
            active_count=len(settings),
        )
