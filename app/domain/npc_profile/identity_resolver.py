"""确定性身份解析（Ticket 07 / Step A）。

把 Scene Facts 的 ``npc_mentions``（本章人物簇 + alias 关系）与既有档案做确定性
归并，代替旧架构由 NPC LLM 做的身份判断：

- 唯一命中既有档案的规范名 / 永久 alias / transient alias → existing；
- 本章新称呼通过 ``alias_relations`` 连接到已解析人物 → 本轮归并到同一档案；
- ``explicit_fixed_alias`` 关系形成待接受 alias change；``temporary_address`` /
  ``same_scene_coreference`` 只用于本轮身份归并，不持久化；
- 无法连接到任何档案的「有名人物」→ 最小候选交给确认 Director（匿名 / 组织 /
  地点由 Scene Facts 不列为 NPC，天然排除）；
- 玩家身份永不解析、永不当候选。

宁漏勿错规则：

- 同一 NPC 的多个 person group 按 ``npc_id`` 合并（observed surfaces / alias
  relations 并集，pending alias 不重复）；
- 某人物簇的多个称呼命中不同档案（歧义）时不强行解析、也不建候选；
- 待接受 alias 做全局协调：同一规范化 alias 被分配给多个不同 NPC 时全部丢弃；
- 正文扫描兜底只使用 ``variant_index`` 中恰好唯一映射一个档案的 variant
  （共享 alias 的歧义变体不 resolve 任何档案）。

另有正文扫描并集兜底：既有 NPC 的规范名 / alias 出现在正文即并入 resolved，
避免 Scene Facts 偶发漏报时既有档案更新一起消失。

本模块不发起任何 LLM 调用；全部规则确定、排序显式、可单测。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.text import eligible_highlight_term, normalize_compact_key
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_facts.schemas import AliasRelation, SceneNpcMention


@dataclass(frozen=True)
class ResolvedNpc:
    """本章归并到既有档案的一个 NPC：本轮观察到的表面称呼 + alias 关系 + 证据。"""

    npc_id: str
    canonical_name: str
    observed_surfaces: tuple[str, ...] = ()
    alias_relations: tuple[AliasRelation, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()
    evidence_paragraph_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingAliasChange:
    """``explicit_fixed_alias`` 形成的待接受 alias 变化（确定性，非模型判断）。"""

    npc_id: str
    canonical_name: str
    surface: str
    evidence_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NpcCandidate:
    """无法连接既有档案的有名人物最小候选（交给确认 Director）。

    ``evidence_fact_ids`` / ``evidence_paragraph_ids`` 为人物出现的证据（mention-
    level 与 alias relation 的证据并集），供 Director 判断是否值得 enrich。
    """

    primary_surface: str
    observed_surfaces: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()
    evidence_paragraph_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityResolutionDiagnostics:
    groups: int
    resolved_groups: int
    ambiguous_groups: int
    unmatched_groups: int
    scan_union_added: int
    pending_aliases: int
    alias_conflicts: int


@dataclass(frozen=True)
class NpcIdentityResolution:
    resolved: tuple[ResolvedNpc, ...] = ()
    pending_alias_changes: tuple[PendingAliasChange, ...] = ()
    candidates: tuple[NpcCandidate, ...] = ()
    diagnostics: IdentityResolutionDiagnostics | None = None


def resolve_npc_identities(
    *,
    profiles: list[NPCProfile],
    npc_mentions: list[SceneNpcMention],
    paragraph_texts: list[str],
    player_name: str,
) -> NpcIdentityResolution:
    """确定性地把本章人物簇归并到既有档案，并派生候选与待接受 alias。"""
    player = normalize_compact_key(player_name)
    by_id = {profile.npc_id: profile for profile in profiles}
    player_profiles = {
        profile.npc_id
        for profile in profiles
        if any(
            normalize_compact_key(value) == player
            for value in [profile.name, *profile.aliases, *profile.transient_aliases]
        )
    }
    variant_index = _build_variant_index(profiles, player_profiles)

    resolved_by_id: dict[str, ResolvedNpc] = {}
    candidates: list[NpcCandidate] = []
    pending: list[PendingAliasChange] = []
    ambiguous = 0
    unmatched = 0
    alias_conflicts = 0

    for mention in npc_mentions:
        if _mention_contains_player(mention, player):
            continue
        mention_surfaces = _mention_surfaces(mention)
        all_surfaces = _distinct_surfaces(mention)
        direct_ids = _matched_ids(mention_surfaces, variant_index)
        all_ids = _matched_ids(all_surfaces, variant_index)
        if not all_ids:
            candidates.append(NpcCandidate(
                primary_surface=mention.primary_surface,
                observed_surfaces=tuple(mention_surfaces),
                evidence_fact_ids=_union_evidence_fact_ids(mention),
                evidence_paragraph_ids=_union_evidence_paragraph_ids(mention),
            ))
            unmatched += 1
        elif len(all_ids) == 1:
            alias_conflicts += _resolve_group(
                npc_id=next(iter(all_ids)),
                mention=mention,
                mention_surfaces=mention_surfaces,
                resolved_by_id=resolved_by_id,
                pending=pending,
                by_id=by_id,
            )
        elif len(direct_ids) == 1:
            # 直接面唯一命中既有档案，但某 alias 关系面与其它档案冲突：仍归并直接
            # 命中者，冲突的 alias 关系被丢弃并计入 alias_conflicts。
            alias_conflicts += _resolve_group(
                npc_id=next(iter(direct_ids)),
                mention=mention,
                mention_surfaces=mention_surfaces,
                resolved_by_id=resolved_by_id,
                pending=pending,
                by_id=by_id,
            )
        else:
            # 宁漏勿错：直接面命中多个不同档案，不强行解析、也不建候选。
            ambiguous += 1

    # 未匹配候选按规范化 primary 合并：同一人物多个 mention group → 单候选
    # （防 Director 重复建档）。
    candidates = _dedupe_candidates(candidates)

    # 全局 alias 协调：同一规范化 alias 分配给多个不同 NPC → 全部丢弃并计冲突。
    pending, pending_conflicts = _reconcile_global_aliases(pending)
    alias_conflicts += pending_conflicts

    scan_union = 0
    segments = [normalize_compact_key(text) for text in paragraph_texts if text]
    for profile in _profiles_sorted(profiles):
        if profile.npc_id in player_profiles or profile.npc_id in resolved_by_id:
            continue
        scanned = _scan_unambiguous_variants(profile, variant_index, segments)
        if scanned:
            resolved_by_id[profile.npc_id] = ResolvedNpc(
                npc_id=profile.npc_id,
                canonical_name=profile.name,
                observed_surfaces=tuple(scanned),
            )
            scan_union += 1

    resolved = tuple(
        sorted(
            resolved_by_id.values(),
            key=lambda item: (
                normalize_compact_key(item.canonical_name),
                item.npc_id,
            ),
        )
    )
    pending_sorted = tuple(
        sorted(
            pending,
            key=lambda item: (
                normalize_compact_key(item.canonical_name),
                item.npc_id,
                item.surface,
            ),
        )
    )
    candidates_sorted = tuple(
        sorted(
            candidates,
            key=lambda item: (
                normalize_compact_key(item.primary_surface),
                item.primary_surface,
            ),
        )
    )

    diagnostics = IdentityResolutionDiagnostics(
        groups=len(npc_mentions),
        resolved_groups=len(resolved_by_id) - scan_union,
        ambiguous_groups=ambiguous,
        unmatched_groups=unmatched,
        scan_union_added=scan_union,
        pending_aliases=len(pending),
        alias_conflicts=alias_conflicts,
    )
    return NpcIdentityResolution(
        resolved=resolved,
        pending_alias_changes=pending_sorted,
        candidates=candidates_sorted,
        diagnostics=diagnostics,
    )


def surface_is_known(surface: str, profile: NPCProfile) -> bool:
    """mention hit policy：只认 canonical name 与永久 alias（含本轮已接受持久化的）。

    transient alias 与未持久化的 alias 不在此列——它们只进 coverage。规范化与身份
    匹配/候选池一致使用 ``normalize_compact_key``。
    """
    key = normalize_compact_key(surface)
    if not key:
        return False
    return key == normalize_compact_key(profile.name) or any(
        normalize_compact_key(alias) == key for alias in profile.aliases
    )


def _reconcile_global_aliases(
    pending: list[PendingAliasChange],
) -> tuple[list[PendingAliasChange], int]:
    """全局 alias 协调：同一规范化 surface 指向 >1 个 npc_id 时全部丢弃。

    返回 ``(reconciled, conflicts)``。
    """
    by_surface: dict[str, set[str]] = {}
    for change in pending:
        by_surface.setdefault(
            normalize_compact_key(change.surface), set()
        ).add(change.npc_id)
    reconciled = [
        change
        for change in pending
        if len(by_surface[normalize_compact_key(change.surface)]) == 1
    ]
    conflicts = sum(
        1
        for change in pending
        if len(by_surface[normalize_compact_key(change.surface)]) > 1
    )
    return reconciled, conflicts


def _scan_unambiguous_variants(
    profile: NPCProfile,
    variant_index: dict[str, set[str]],
    segments: list[str],
) -> list[str]:
    scanned: list[str] = []
    for variant in _profile_variants(profile):
        compact = normalize_compact_key(variant)
        if not eligible_highlight_term(compact):
            continue
        ids = variant_index.get(compact, set())
        if len(ids) != 1 or next(iter(ids)) != profile.npc_id:
            continue
        if not any(compact in segment for segment in segments):
            continue
        scanned.append(variant)
    return scanned


def _build_variant_index(
    profiles: list[NPCProfile],
    player_profiles: set[str],
) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for profile in profiles:
        if profile.npc_id in player_profiles:
            continue
        for variant in _profile_variants(profile):
            key = normalize_compact_key(variant)
            if key:
                index.setdefault(key, set()).add(profile.npc_id)
    return index


def _profile_variants(profile: NPCProfile) -> list[str]:
    return [profile.name, *profile.aliases, *profile.transient_aliases.keys()]


def _profiles_sorted(profiles: list[NPCProfile]) -> list[NPCProfile]:
    return sorted(
        profiles,
        key=lambda profile: (normalize_compact_key(profile.name), profile.npc_id),
    )


def _mention_surfaces(mention: SceneNpcMention) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for surface in [mention.primary_surface, *mention.observed_surfaces]:
        key = normalize_compact_key(surface)
        if key and key not in seen:
            seen.add(key)
            merged.append(surface)
    return merged


def _distinct_surfaces(mention: SceneNpcMention) -> list[str]:
    surfaces = [
        *mention.observed_surfaces,
        *(relation.surface for relation in mention.alias_relations),
    ]
    if not mention.primary_surface:
        return surfaces
    seen: set[str] = set()
    merged: list[str] = []
    for surface in [mention.primary_surface, *surfaces]:
        key = normalize_compact_key(surface)
        if key and key not in seen:
            seen.add(key)
            merged.append(surface)
    return merged


def _mention_contains_player(mention: SceneNpcMention, player: str) -> bool:
    """玩家保护覆盖整个人物簇：任一 surface（primary + observed + alias relation）
    归一化等于玩家 → 整组跳过（不解析、不候选）。"""
    surfaces = [
        mention.primary_surface,
        *mention.observed_surfaces,
        *(relation.surface for relation in mention.alias_relations),
    ]
    return any(
        normalize_compact_key(surface) == player
        for surface in surfaces
    )


def _dedupe_candidates(candidates: list[NpcCandidate]) -> list[NpcCandidate]:
    """同一规范化 primary 的多个未匹配人物簇合并为单候选。"""
    by_primary: dict[str, NpcCandidate] = {}
    for candidate in candidates:
        key = normalize_compact_key(candidate.primary_surface)
        existing = by_primary.get(key)
        if existing is None:
            by_primary[key] = candidate
            continue
        by_primary[key] = NpcCandidate(
            primary_surface=existing.primary_surface,
            observed_surfaces=tuple(
                _merge_surfaces(existing.observed_surfaces, candidate.observed_surfaces)
            ),
            evidence_fact_ids=tuple(
                sorted({*existing.evidence_fact_ids, *candidate.evidence_fact_ids})
            ),
            evidence_paragraph_ids=tuple(
                sorted(
                    {
                        *existing.evidence_paragraph_ids,
                        *candidate.evidence_paragraph_ids,
                    }
                )
            ),
        )
    return list(by_primary.values())


def _union_evidence_paragraph_ids(mention: SceneNpcMention) -> tuple[str, ...]:
    return tuple(sorted(set(mention.evidence_paragraph_ids)))


def _union_evidence_fact_ids(mention: SceneNpcMention) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *mention.evidence_fact_ids,
                *(
                    fact_id
                    for relation in mention.alias_relations
                    for fact_id in relation.evidence_fact_ids
                ),
            }
        )
    )


def _matched_ids(
    surfaces: list[str],
    variant_index: dict[str, set[str]],
) -> set[str]:
    return {
        npc_id
        for surface in surfaces
        for npc_id in variant_index.get(normalize_compact_key(surface), set())
    }


def _resolve_group(
    *,
    npc_id: str,
    mention: SceneNpcMention,
    mention_surfaces: list[str],
    resolved_by_id: dict[str, ResolvedNpc],
    pending: list[PendingAliasChange],
    by_id: dict[str, NPCProfile],
) -> int:
    """把一个人物簇归并到指定档案；同 npc_id 已有簇则合并（不覆盖）。"""
    profile = by_id[npc_id]
    mention_facts = _union_evidence_fact_ids(mention)
    mention_paragraphs = _union_evidence_paragraph_ids(mention)
    existing = resolved_by_id.get(npc_id)
    if existing is None:
        resolved_by_id[npc_id] = ResolvedNpc(
            npc_id=npc_id,
            canonical_name=profile.name,
            observed_surfaces=tuple(mention_surfaces),
            alias_relations=tuple(mention.alias_relations),
            evidence_fact_ids=mention_facts,
            evidence_paragraph_ids=mention_paragraphs,
        )
    else:
        resolved_by_id[npc_id] = ResolvedNpc(
            npc_id=npc_id,
            canonical_name=profile.name,
            observed_surfaces=tuple(
                _merge_surfaces(existing.observed_surfaces, mention_surfaces)
            ),
            alias_relations=tuple(
                _merge_relations(existing.alias_relations, mention.alias_relations)
            ),
            evidence_fact_ids=tuple(
                sorted({*existing.evidence_fact_ids, *mention_facts})
            ),
            evidence_paragraph_ids=tuple(
                sorted({*existing.evidence_paragraph_ids, *mention_paragraphs})
            ),
        )
    conflicts = 0
    for relation in mention.alias_relations:
        if relation.basis != "explicit_fixed_alias":
            continue
        status = _maybe_pending_alias(pending, profile, relation, by_id)
        if status == "conflict":
            conflicts += 1
    return conflicts


def _merge_surfaces(*groups: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for surface in group:
            key = normalize_compact_key(surface)
            if key and key not in seen:
                seen.add(key)
                merged.append(surface)
    return merged


def _merge_relations(*groups: Sequence[AliasRelation]) -> list[AliasRelation]:
    seen: set[tuple[str, str]] = set()
    merged: list[AliasRelation] = []
    for group in groups:
        for relation in group:
            key = (normalize_compact_key(relation.surface), relation.basis)
            if key not in seen:
                seen.add(key)
                merged.append(relation)
    return merged


def _maybe_pending_alias(
    pending: list[PendingAliasChange],
    profile: NPCProfile,
    relation: AliasRelation,
    by_id: dict[str, NPCProfile],
) -> str:
    """尝试把 ``explicit_fixed_alias`` 表面收为待接受 alias。

    返回 ``added`` / ``known``（已是自身身份变体或已入列）/ ``conflict``（与他档
    持久化身份冲突）。
    """
    key = normalize_compact_key(relation.surface)
    if not key:
        return "known"
    own_keys = {
        normalize_compact_key(value)
        for value in [profile.name, *profile.aliases, *profile.transient_aliases]
    }
    if key in own_keys:
        return "known"
    for index, change in enumerate(pending):
        if (
            change.npc_id == profile.npc_id
            and normalize_compact_key(change.surface) == key
        ):
            # 已入列：合并新增证据（f1 ∪ f2），不重复添加。
            pending[index] = PendingAliasChange(
                npc_id=change.npc_id,
                canonical_name=change.canonical_name,
                surface=change.surface,
                evidence_fact_ids=tuple(
                    sorted({*change.evidence_fact_ids, *relation.evidence_fact_ids})
                ),
            )
            return "added"
    for other_id, other in by_id.items():
        if other_id == profile.npc_id:
            continue
        other_keys = {
            normalize_compact_key(value)
            for value in [other.name, *other.aliases, *other.transient_aliases]
        }
        if key in other_keys:
            return "conflict"
    pending.append(PendingAliasChange(
        npc_id=profile.npc_id,
        canonical_name=profile.name,
        surface=relation.surface,
        evidence_fact_ids=tuple(sorted(set(relation.evidence_fact_ids))),
    ))
    return "added"
