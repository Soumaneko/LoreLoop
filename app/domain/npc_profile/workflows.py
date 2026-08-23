from app.core.text import normalize_compact_key, normalize_key
from app.domain.action.schemas import Anchor
from app.domain.confirmed_history import HistoryDocument
from app.domain.core_memory.schemas import CoreMemory
from app.domain.creative_brief import (
    CreativeBrief,
    draw_desire_pattern,
)
from app.domain.npc_profile.backfiller import NPCProfileBackfiller
from app.domain.npc_profile.director_promotion import (
    npc_profile_from_spontaneous_material,
)
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_facts import SceneFactSet


def _npc_mentioned_in_scene_facts(name: str, scene_facts: SceneFactSet | None) -> bool:
    """检查 NPC 是否在 Scene Facts 中出现（无论是 appeared 还是 mentioned_only）。"""
    if scene_facts is None:
        return True
    key = normalize_compact_key(name)
    if not key:
        return False
    for mention in getattr(scene_facts, "npc_mentions", []):
        if normalize_compact_key(mention.primary_surface) == key:
            return True
        for relation in getattr(mention, "alias_relations", []):
            if normalize_compact_key(relation.surface) == key:
                return True
    return False


def auto_backfill_spontaneous_npc_profiles(
    *,
    pending,
    npc_store: NPCProfileStore,
    gateway,
    core_memory: CoreMemory,
    turn_index: int,
    scene_id: str | None = None,
    scene_facts: SceneFactSet | None = None,
    trace_id: str | None = None,
) -> list[str]:
    """对本轮生成导演引入的即兴轻量 NPC 执行自动 Backfill 并直接写入 NPCProfileStore。"""
    stage = getattr(pending, "director_stage", None)
    if stage is None or not getattr(stage, "materials", None):
        return []

    spontaneous_materials = [
        mat
        for mat in stage.materials
        if mat.kind == "npc" and (mat.payload or {}).get("source") == "spontaneous"
    ]
    if not spontaneous_materials:
        return []

    backfilled_npc_ids: list[str] = []
    backfiller = NPCProfileBackfiller(gateway)

    for material in spontaneous_materials:
        payload = material.payload or {}
        name = payload.get("name")
        if not name:
            continue

        # 若正文中完全未出现（既没有 appeared 也没有 mentioned_only），跳过 Backfill，避免幽灵档案
        if scene_facts is not None and not _npc_mentioned_in_scene_facts(name, scene_facts):
            continue

        # 若已存在同名或同 ID 档案，保护不重复创建
        existing_profile = npc_store.get_by_name(name)
        if existing_profile is not None:
            backfilled_npc_ids.append(existing_profile.npc_id)
            continue

        base_profile = npc_profile_from_spontaneous_material(
            material,
            turn_index=turn_index,
            scene_id=scene_id,
        )

        desire_pattern = draw_desire_pattern()
        existing_all = npc_store.list_all()
        backfilled = backfiller.backfill(
            profile=base_profile,
            existing_profiles=existing_all,
            core_memory=core_memory,
            trace_id=trace_id,
            guidance=material.guidance,
            creative_brief=desire_pattern,
        )
        # 强制锁定原名不被篡改
        backfilled.name = base_profile.name

        saved_id = npc_store.upsert(backfilled)
        backfilled_npc_ids.append(saved_id)

    return backfilled_npc_ids


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
