from __future__ import annotations

import re

from app.domain.action.schemas import ActionRetrievalTopic, Anchor
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import NPCProfileDirectorView

from .schemas import (
    ContextSearchEntity,
    ContextSearchIntent,
    ContextSearchPlan,
)


_TOPIC_STOPWORDS = (
    "这件事情", "这件事", "那件事情", "那件事", "有关", "关于", "针对",
    "有没有", "是否", "我们", "自己", "这个", "那个", "这些", "那些",
    "然后", "接着", "继续", "尝试", "想要", "打算", "决定", "开始",
    "进行", "一下", "向", "对", "和", "与", "把", "让", "告诉",
    "询问", "问", "我", "你", "他", "她", "它", "的", "了", "着",
)


def build_context_search_plan(
    *,
    user_input: str,
    current_state: CurrentState,
    matched_anchors: list[Anchor],
    npc_profiles: list[NPCProfileDirectorView],
    retrieval_topics: list[ActionRetrievalTopic] | None = None,
) -> ContextSearchPlan:
    profile_entities = _profile_entities(npc_profiles)
    intents: list[ContextSearchIntent] = []
    entity_intent_indexes: dict[str, int] = {}
    topic_intent_indexes: dict[str, int] = {}
    relevant_characters: list[ContextSearchEntity] = []
    normalized_input = user_input.casefold()

    def add_or_promote_entity_intent(
        entity: ContextSearchEntity,
        *,
        focus: str,
        origin: str,
    ) -> None:
        key = entity.name.casefold()
        existing_index = entity_intent_indexes.get(key)
        if existing_index is not None:
            existing = intents[existing_index]
            if _ORIGIN_PRIORITY[origin] < _ORIGIN_PRIORITY[existing.origin]:
                intents[existing_index] = existing.model_copy(update={
                    "origin": origin,
                })
            return
        entity_intent_indexes[key] = len(intents)
        intents.append(ContextSearchIntent(
            intent_id=f"i{len(intents) + 1}",
            focus=focus,
            origin=origin,
            entities=[entity],
        ))

    def add_or_promote_topic_intent(topic: str, *, origin: str) -> None:
        clean = topic.strip()
        if not clean:
            return
        key = clean.casefold()
        existing_index = topic_intent_indexes.get(key)
        if existing_index is not None:
            existing = intents[existing_index]
            if _ORIGIN_PRIORITY[origin] < _ORIGIN_PRIORITY[existing.origin]:
                intents[existing_index] = existing.model_copy(update={
                    "origin": origin,
                })
            return
        topic_intent_indexes[key] = len(intents)
        intents.append(ContextSearchIntent(
            intent_id=f"i{len(intents) + 1}",
            focus="topic",
            origin=origin,
            topics=[clean],
        ))

    def promote_matching_entity(topic: str) -> bool:
        """Prefer entity semantics for an exact known name or alias."""
        key = topic.strip().casefold()
        if not key:
            return False
        for index, intent in enumerate(intents):
            if intent.focus not in {"entity", "location"}:
                continue
            if not any(
                key == surface.strip().casefold()
                for entity in intent.entities
                for surface in [entity.name, *entity.aliases]
                if surface.strip()
            ):
                continue
            if intent.origin != "explicit_input":
                intents[index] = intent.model_copy(update={
                    "origin": "explicit_input",
                })
            return True
        return False

    location = _resolve_entity(current_state.scene.location, profile_entities)
    if location is not None:
        add_or_promote_entity_intent(
            location,
            focus="location",
            origin=(
                "explicit_input"
                if _entity_mentioned(normalized_input, location)
                else "current_context"
            ),
        )

    player_name = current_state.player.name.strip()
    for participant in current_state.scene.participant_names:
        clean = participant.strip()
        if not clean or clean.casefold() == player_name.casefold():
            continue
        entity = _resolve_entity(clean, profile_entities)
        if entity is None:
            continue
        relevant_characters.append(entity)
        add_or_promote_entity_intent(
            entity,
            focus="entity",
            origin=(
                "explicit_input"
                if _entity_mentioned(normalized_input, entity)
                else "current_context"
            ),
        )

    for entity in profile_entities:
        if not _entity_mentioned(normalized_input, entity):
            continue
        relevant_characters.append(entity)
        add_or_promote_entity_intent(
            entity,
            focus="entity",
            origin="explicit_input",
        )

    for anchor in matched_anchors:
        clean = (anchor.normalized or anchor.text).strip()
        if not clean:
            continue
        origin = (
            "explicit_input"
            if anchor.source == "explicit"
            else "matched_anchor"
        )
        if anchor.kind in {"concept", "action_keyword", "unknown"}:
            add_or_promote_topic_intent(clean, origin=origin)
            continue
        entity = _resolve_entity(clean, profile_entities)
        if entity is None:
            entity = ContextSearchEntity(name=clean)
        add_or_promote_entity_intent(
            entity,
            focus="location" if anchor.kind == "location" else "entity",
            origin=origin,
        )
        if anchor.kind == "character":
            relevant_characters.append(entity)

    relationship_entities = _unique_entities([
        _resolve_entity(player_name, profile_entities)
        or ContextSearchEntity(name=player_name),
        *relevant_characters,
    ])[:5]
    if len(relationship_entities) >= 2:
        intents.append(ContextSearchIntent(
            intent_id=f"i{len(intents) + 1}",
            focus="relationship",
            origin="relationship",
            entities=relationship_entities,
        ))

    topics = (
        [topic.text for topic in retrieval_topics]
        if retrieval_topics
        else _extract_topics(
            user_input,
            surfaces=[
                surface
                for entity in profile_entities
                for surface in [entity.name, *entity.aliases]
            ],
        )
    )
    for topic in topics:
        if promote_matching_entity(topic):
            continue
        add_or_promote_topic_intent(topic, origin="explicit_input")

    return ContextSearchPlan(intents=intents)


_ORIGIN_PRIORITY = {
    "explicit_input": 0,
    "matched_anchor": 1,
    "current_context": 2,
    "relationship": 3,
}


def _profile_entities(
    npc_profiles: list[NPCProfileDirectorView],
) -> list[ContextSearchEntity]:
    return _unique_entities([
        *(
            ContextSearchEntity(name=profile.name, aliases=profile.aliases)
            for profile in npc_profiles
        ),
    ])


def _resolve_entity(
    raw_name: str,
    entities: list[ContextSearchEntity],
) -> ContextSearchEntity | None:
    clean = str(raw_name or "").strip()
    if not clean:
        return None
    key = clean.casefold()
    for entity in entities:
        if key in {
            surface.casefold()
            for surface in [entity.name, *entity.aliases]
            if surface.strip()
        }:
            return entity
    return ContextSearchEntity(name=clean)


def _entity_mentioned(text: str, entity: ContextSearchEntity) -> bool:
    folded_text = text.casefold()
    for surface in [entity.name, *entity.aliases]:
        clean = surface.strip().casefold()
        if not clean:
            continue
        if re.search(r"[\u4e00-\u9fff]", clean):
            if len(clean) >= 2 and clean in folded_text:
                return True
            if len(clean) == 1 and any(
                f"{left}{clean}{right}" in folded_text
                for left, right in (("“", "”"), ('"', '"'), ("《", "》"))
            ):
                return True
            continue
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(clean)}(?![A-Za-z0-9_])",
            folded_text,
        ):
            return True
    return False


def _unique_entities(
    entities: list[ContextSearchEntity],
) -> list[ContextSearchEntity]:
    result: list[ContextSearchEntity] = []
    seen: set[str] = set()
    for entity in entities:
        key = entity.name.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return result


def _extract_topics(user_input: str, *, surfaces: list[str]) -> list[str]:
    text = user_input
    for surface in sorted(
        {surface.strip() for surface in surfaces if surface.strip()},
        key=len,
        reverse=True,
    ):
        text = re.sub(re.escape(surface), " ", text, flags=re.IGNORECASE)
    for stopword in _TOPIC_STOPWORDS:
        text = text.replace(stopword, " ")

    candidates: list[str] = []
    for quoted in re.findall(r"[《“\"]([^》”\"]{2,16})[》”\"]", user_input):
        candidates.append(quoted.strip())
    for segment in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", text):
        if len(segment) <= 8:
            candidates.append(segment)
            continue
        candidates.extend(
            segment[index:index + 4]
            for index in range(0, len(segment) - 2, 2)
        )

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        clean = candidate.strip()
        key = clean.casefold()
        minimum_length = 2 if re.search(r"[\u4e00-\u9fff]", clean) else 3
        if len(clean) < minimum_length or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= 16:
            break
    return result
