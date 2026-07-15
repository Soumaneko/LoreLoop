from dataclasses import dataclass

from app.domain.action.schemas import Anchor
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfile
from app.domain.entity.store import EntityStore
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore


@dataclass
class ContextProfileSelection:
    entity_profiles: list[EntityProfile]
    npc_profiles: list[NPCProfile]
    anchors: list[Anchor]


def select_context_profiles(
    *,
    user_input: str,
    current_state: CurrentState,
    entity_store: EntityStore,
    npc_store: NPCProfileStore,
    limit: int = 20,
) -> ContextProfileSelection:
    text = _norm(user_input)
    entity_profiles = _select_entities(
        user_input=text,
        current_state=current_state,
        entity_store=entity_store,
        limit=limit,
    )
    npc_profiles = _select_npcs(
        user_input=text,
        current_state=current_state,
        npc_store=npc_store,
        limit=limit,
    )
    anchors = _build_anchors(entity_profiles, npc_profiles)
    return ContextProfileSelection(
        entity_profiles=entity_profiles,
        npc_profiles=npc_profiles,
        anchors=anchors,
    )


def _select_entities(
    *,
    user_input: str,
    current_state: CurrentState,
    entity_store: EntityStore,
    limit: int,
) -> list[EntityProfile]:
    explicit_names = {_norm(name) for name in _state_entity_names(current_state)}
    selected: list[EntityProfile] = []
    seen: set[str] = set()

    for profile in entity_store.list_all():
        matched = _profile_in_names(
            explicit_names,
            profile.name,
            profile.aliases,
        ) or _profile_mentioned(user_input, profile.name, profile.aliases)
        if not matched or profile.entity_id in seen:
            continue
        selected.append(profile)
        seen.add(profile.entity_id)
        if len(selected) >= limit:
            break
    return selected


def _select_npcs(
    *,
    user_input: str,
    current_state: CurrentState,
    npc_store: NPCProfileStore,
    limit: int,
) -> list[NPCProfile]:
    explicit_names = {_norm(name) for name in _state_npc_names(current_state)}
    selected: list[NPCProfile] = []
    seen: set[str] = set()

    for profile in npc_store.list_all():
        matched = _profile_in_names(
            explicit_names,
            profile.name,
            profile.aliases,
        ) or _profile_mentioned(user_input, profile.name, profile.aliases)
        if not matched or profile.npc_id in seen:
            continue
        selected.append(profile)
        seen.add(profile.npc_id)
        if len(selected) >= limit:
            break
    return selected


def _build_anchors(
    entity_profiles: list[EntityProfile],
    npc_profiles: list[NPCProfile],
) -> list[Anchor]:
    anchors: list[Anchor] = []
    seen: set[str] = set()

    for profile in npc_profiles:
        _append_anchor(
            anchors,
            seen,
            text=profile.name,
            kind="character",
        )

    for profile in entity_profiles:
        kind = _entity_anchor_kind(profile.entity_type)
        _append_anchor(
            anchors,
            seen,
            text=profile.name,
            kind=kind,
        )
    return anchors


def _append_anchor(
    anchors: list[Anchor],
    seen: set[str],
    *,
    text: str,
    kind: str,
) -> None:
    clean = text.strip()
    key = clean.casefold()
    if not clean or key in seen:
        return
    seen.add(key)
    anchors.append(
        Anchor(
            text=clean,
            normalized=clean,
            kind=kind,  # type: ignore[arg-type]
            source="explicit",
            confidence=0.9,
        )
    )


def _profile_mentioned(user_input: str, name: str, aliases: list[str]) -> bool:
    variants = [name, *aliases]
    return any(_norm(value) and _norm(value) in user_input for value in variants)


def _profile_in_names(names: set[str], name: str, aliases: list[str]) -> bool:
    return any(
        normalized and normalized in names
        for value in [name, *aliases]
        if (normalized := _norm(value))
    )


def _state_entity_names(current_state: CurrentState) -> list[str]:
    names = [
        current_state.scene.location,
        *current_state.scene.participant_names,
        current_state.player.name,
    ]
    resources = current_state.resources
    for item in resources.inventory:
        names.append(item.name)
    names.extend(resources.fixed_assets)
    names.extend(resources.temporary_assets)
    return _unique(names)


def _state_npc_names(current_state: CurrentState) -> list[str]:
    names = [
        *current_state.scene.participant_names,
        current_state.player.name,
    ]
    return _unique(names)


def _entity_anchor_kind(entity_type: str) -> str:
    if entity_type in {"character", "location", "item", "organization"}:
        return entity_type
    return "concept"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _norm(value: str) -> str:
    return str(value or "").strip().casefold()
