from dataclasses import dataclass

from app.domain.action.schemas import Anchor
from app.domain.current_state.schemas import CurrentState
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


def _profile_in_names(names: set[str], name: str, aliases: list[str]) -> bool:
    return any(
        normalized and normalized in names
        for value in [name, *aliases]
        if (normalized := _norm(value))
    )


def _norm(value: str) -> str:
    return "".join(str(value or "").casefold().split())
