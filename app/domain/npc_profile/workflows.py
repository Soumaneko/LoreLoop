from __future__ import annotations

from app.core.text import normalize_key
from app.domain.action.schemas import Anchor
from app.domain.confirmed_history import HistoryDocument
from app.domain.core_memory.schemas import CoreMemory
from app.domain.creative_brief import CreativeBrief, draw_creative_brief
from app.domain.npc_profile.backfiller import NPCProfileBackfiller
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.current_state.schemas import CurrentState

TRANSIENT_ALIAS_TURNS = 3


def attach_transient_alias_for_rename(
    profile: NPCProfile,
    *,
    old_name: str,
    turn_index: int,
    expiry_turns: int = TRANSIENT_ALIAS_TURNS,
) -> None:
    """把改名前的代称挂为临时别名，避免出现“查无此人”的认知断层。

    临时别名会在 ``turn_index + expiry_turns`` 之后由回合推进机制清理，
    不参与永久别名的命中计数与晋升。
    """
    clean = str(old_name).strip()
    if not clean:
        return
    key = normalize_key(clean)
    known = {
        normalize_key(profile.name),
        *(normalize_key(alias) for alias in profile.aliases),
        *(normalize_key(alias) for alias in profile.transient_aliases),
    }
    if key in known:
        return
    profile.transient_aliases[clean] = turn_index + expiry_turns


def prune_expired_transient_aliases(
    profile: NPCProfile,
    *,
    turn_index: int,
) -> bool:
    """删除已过期的临时别名，返回是否发生变化。"""
    expired = [
        alias
        for alias, expiry in profile.transient_aliases.items()
        if expiry < turn_index
    ]
    for alias in expired:
        profile.transient_aliases.pop(alias, None)
    return bool(expired)


def prune_npc_transient_aliases_from_current_state(
    *,
    npc_store: NPCProfileStore,
    turn_index: int | None = None,
) -> list[str]:
    saved_ids: list[str] = []
    for profile in npc_store.list_all():
        aliases_pruned = (
            prune_expired_transient_aliases(
                profile,
                turn_index=turn_index,
            )
            if turn_index is not None
            else False
        )
        if aliases_pruned:
            saved_ids.append(npc_store.upsert(profile))
    return saved_ids


def gather_npc_profiles_from_state_and_anchors(
    *,
    current_state: CurrentState,
    anchors: list[Anchor],
    npc_store: NPCProfileStore,
    limit: int = 20,
) -> list[NPCProfile]:
    names = _gather_names(current_state, anchors)
    selected: list[NPCProfile] = []
    seen: set[str] = set()
    for name in names:
        profile = npc_store.get_by_name(name)
        if profile is None or profile.npc_id in seen:
            continue
        selected.append(profile)
        seen.add(profile.npc_id)
        if len(selected) >= limit:
            break
    return selected


def backfill_new_npc_profile(
    *,
    profile: NPCProfile,
    existing_profiles: list[NPCProfile],
    core_memory: CoreMemory,
    gateway,
    npc_store: NPCProfileStore,
    settings: list | None = None,
    trace_id: str | None = None,
    guidance: str | None = None,
    creative_brief: CreativeBrief | None = None,
) -> NPCProfile:
    backfiller = NPCProfileBackfiller(gateway)
    return backfiller.backfill(
        profile=profile,
        existing_profiles=existing_profiles,
        core_memory=core_memory,
        settings=settings,
        trace_id=trace_id,
        guidance=guidance,
        creative_brief=creative_brief or draw_creative_brief(),
    )


def _matches_profile_name_or_alias(value: str | None, profile: NPCProfile) -> bool:
    key = normalize_key(value)
    if not key:
        return False
    return (
        key == normalize_key(profile.name)
        or key in {normalize_key(alias) for alias in profile.aliases}
        or key in {normalize_key(alias) for alias in profile.transient_aliases}
    )


def _gather_names(current_state: CurrentState, anchors: list[Anchor]) -> list[str]:
    names: list[str] = []
    names.extend(current_state.scene.participant_names)
    names.append(current_state.player.name)
    for anchor in anchors:
        if anchor.kind == "character":
            names.append(anchor.normalized or anchor.text)
            names.append(anchor.text)
    return _merge_unique(names)


def _merge_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        clean = str(value).strip()
        key = normalize_key(clean)
        if clean and key not in seen:
            seen.add(key)
            merged.append(clean)
    return merged
