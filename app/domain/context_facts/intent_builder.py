from __future__ import annotations

import re

from app.domain.action.schemas import Anchor
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import NPCProfileDirectorView

from .schemas import (
    ContextSearchEntity,
    ContextSearchIntent,
    ContextSearchPlan,
)


_TOPIC_STOPWORDS = (
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
) -> ContextSearchPlan:
    profile_entities = _profile_entities(npc_profiles)
    intents: list[ContextSearchIntent] = []
    seen_entities: set[str] = set()
    relevant_entities: list[ContextSearchEntity] = []

    location = _resolve_entity(current_state.scene.location, profile_entities)
    if location is not None:
        intents.append(ContextSearchIntent(
            intent_id="i1",
            focus="location",
            entities=[location],
        ))
        seen_entities.add(location.name.casefold())

    player_name = current_state.player.name.strip()
    for participant in current_state.scene.participant_names:
        clean = participant.strip()
        if not clean or clean.casefold() == player_name.casefold():
            continue
        entity = _resolve_entity(clean, profile_entities)
        if entity is None:
            continue
        relevant_entities.append(entity)
        key = entity.name.casefold()
        if key in seen_entities:
            continue
        intents.append(ContextSearchIntent(
            intent_id=f"i{len(intents) + 1}",
            focus="entity",
            entities=[entity],
        ))
        seen_entities.add(key)

    normalized_input = user_input.casefold()
    for entity in profile_entities:
        if not _entity_mentioned(normalized_input, entity):
            continue
        relevant_entities.append(entity)
        key = entity.name.casefold()
        if key in seen_entities:
            continue
        intents.append(ContextSearchIntent(
            intent_id=f"i{len(intents) + 1}",
            focus="entity",
            entities=[entity],
        ))
        seen_entities.add(key)

    for anchor in matched_anchors:
        clean = (anchor.normalized or anchor.text).strip()
        if not clean:
            continue
        entity = _resolve_entity(clean, profile_entities)
        if entity is None:
            entity = ContextSearchEntity(name=clean)
        relevant_entities.append(entity)
        key = entity.name.casefold()
        if key in seen_entities:
            continue
        intents.append(ContextSearchIntent(
            intent_id=f"i{len(intents) + 1}",
            focus="entity",
            entities=[entity],
        ))
        seen_entities.add(key)

    relationship_entities = _unique_entities([
        _resolve_entity(player_name, profile_entities)
        or ContextSearchEntity(name=player_name),
        *relevant_entities,
    ])[:5]
    if len(relationship_entities) >= 2:
        intents.append(ContextSearchIntent(
            intent_id=f"i{len(intents) + 1}",
            focus="relationship",
            entities=relationship_entities,
        ))

    topics = _extract_topics(
        user_input,
        surfaces=[
            surface
            for entity in profile_entities
            for surface in [entity.name, *entity.aliases]
        ],
    )
    if topics:
        intents.append(ContextSearchIntent(
            intent_id=f"i{len(intents) + 1}",
            focus="topic",
            topics=topics,
        ))

    return ContextSearchPlan(intents=intents)


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
    return any(
        surface.strip() and surface.strip().casefold() in text
        for surface in [entity.name, *entity.aliases]
    )


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
    for segment in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{3,}", text):
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
        if len(clean) < 3 or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= 16:
            break
    return result
