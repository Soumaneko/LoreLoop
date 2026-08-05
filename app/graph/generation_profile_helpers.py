from dataclasses import dataclass

from app.domain.action.schemas import Anchor
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore


@dataclass
class ContextProfileSelection:
    npc_profiles: list[NPCProfile]
    anchors: list[Anchor]


def select_context_profiles(
    *,
    user_input: str,
    current_state: CurrentState,
    npc_store: NPCProfileStore,
    limit: int = 20,
) -> ContextProfileSelection:
    text = _norm(user_input)
    explicit_names = {
        _norm(name)
        for name in [
            *current_state.scene.participant_names,
            current_state.player.name,
        ]
    }
    profiles: list[NPCProfile] = []
    for profile in npc_store.list_all():
        matched = _profile_in_names(
            explicit_names,
            profile.name,
            profile.aliases,
        ) or _profile_mentioned(text, profile.name, profile.aliases)
        if not matched:
            continue
        profiles.append(profile)
        if len(profiles) >= limit:
            break
    return ContextProfileSelection(
        npc_profiles=profiles,
        anchors=[
            Anchor(
                text=profile.name,
                normalized=profile.name,
                kind="character",
                source="explicit",
                confidence=0.9,
            )
            for profile in profiles
        ],
    )


def _profile_mentioned(user_input: str, name: str, aliases: list[str]) -> bool:
    return any(
        _norm(value) and _norm(value) in user_input
        for value in [name, *aliases]
    )


def _profile_texts(profile: NPCProfile) -> list[str]:
    return [
        _norm(value)
        for value in [profile.name, *profile.aliases, *profile.transient_aliases]
        if _norm(value)
    ]


def select_profiles_by_context_facts(
    *,
    context_facts: ContextFactSet | None,
    npc_store: NPCProfileStore,
    limit: int = 20,
) -> list[NPCProfile]:
    """Method 1: match NPC profiles against this turn's context facts.

    Names and aliases are matched as substrings against the turn focus and
    each extracted fact so profiles relevant to the current situation stay
    in scope even when the player never names them.
    """
    if context_facts is None:
        return []
    texts = [
        _norm(context_facts.turn_focus),
        *(_norm(fact.fact) for fact in context_facts.facts),
    ]
    texts = [text for text in texts if text]
    if not texts:
        return []
    selected: list[NPCProfile] = []
    for profile in npc_store.list_all():
        if any(any(variant in text for text in texts) for variant in _profile_texts(profile)):
            selected.append(profile)
            if len(selected) >= limit:
                break
    return selected


def select_profiles_by_active_events(
    *,
    events: list[DirectorEvent],
    npc_store: NPCProfileStore,
    limit: int = 20,
) -> list[NPCProfile]:
    """Method 2: match NPC profiles linked to currently active Director events.

    Actor refs are resolved through the profile id (existing NPC) or the
    Director draft id (already-introduced NPC); name-only actors fall back to
    name/alias matching.
    """
    selected: list[NPCProfile] = []
    seen: set[str] = set()
    for event in events:
        if not event.is_active():
            continue
        for actor in event.actors:
            profile = _resolve_actor_profile(actor, npc_store)
            if profile is None or profile.npc_id in seen:
                continue
            selected.append(profile)
            seen.add(profile.npc_id)
            if len(selected) >= limit:
                return selected
    return selected


def _resolve_actor_profile(actor, npc_store: NPCProfileStore) -> NPCProfile | None:
    ref = actor.ref
    if ref is not None and ref.kind == "npc":
        profile = npc_store.get_by_id(ref.resource_id)
        if profile is None:
            profile = npc_store.get_by_source_director_npc_id(ref.resource_id)
        if profile is not None:
            return profile
    return npc_store.get_by_name(actor.name)


def merge_profile_selections(
    *selections: list[NPCProfile],
    limit: int = 20,
) -> list[NPCProfile]:
    """Dedupe and bound a union of profile selections by npc_id.

    Sources are consumed round-robin so an early-saturating selection (for
    example a full ``select_context_profiles`` batch) never starves the later
    context-fact or active-event sources.
    """
    merged: list[NPCProfile] = []
    seen: set[str] = set()
    pools = [list(selection) for selection in selections if selection]
    while pools and len(merged) < limit:
        for pool in list(pools):
            if not pool:
                pools.remove(pool)
                continue
            profile = pool.pop(0)
            if profile.npc_id in seen:
                continue
            seen.add(profile.npc_id)
            merged.append(profile)
            if len(merged) >= limit:
                break
    return merged


def _profile_in_names(names: set[str], name: str, aliases: list[str]) -> bool:
    return any(
        normalized and normalized in names
        for value in [name, *aliases]
        if (normalized := _norm(value))
    )


def _norm(value: str) -> str:
    return "".join(str(value or "").casefold().split())
