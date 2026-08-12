"""确定性身份候选池（Ticket 02）。

替代「向 Prompt 发送完整 NPC roster」的线性 token 增长方案：从全部档案中
确定性选出一个有界候选池，供 unresolved 表面称呼做 alias 判定，并推导
exact targets（这些目标必须被解析为 existing）。

分层（彼此不重叠）：

1. ``exact``：current_state 参与者精确命中 + 正文片段中的
   name/永久 alias/transient alias 子串命中；exact 前置、永不裁剪；
2. ``ambiguous``：与其它档案共享同一 name/alias/transient variant 的档案
   （歧义 variant 不进入 exact，否则「exact 必须 existing」与「surface 不得重复」
   会形成不可满足契约）；它们只进入 identity options 供 alias_match；
3. ``contextual``：descriptor 字段（role/faction/appearance/public_summary）与
   正文相关但没有 name/alias 命中的档案；只占剩余 cap。

确定性约定：

- 身份匹配走 ``normalize_compact_key(variant) -> sorted npc_ids`` 索引（折叠内部
  空格，与正文子串匹配同一规范化），不依赖输入档案顺序；
- 搜索文本分片独立匹配，绝不拼接，避免跨边界假命中；
- 每个档案的匹配信号独立计数，contextual 排序按
  ``(信号数 desc, 最长匹配长度 desc, 规范名, npc_id)``，不做弱语义纯 name 排序；
- 全部排序显式给出（不依赖 store 的 storage 顺序与 dict 迭代顺序）；
- 玩家身份永不解析为档案、永不进任何 option。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.text import eligible_highlight_term, normalize_compact_key
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.turn_contract import (
    MAX_IDENTITY_OPTIONS,
    NPCIdentityOption,
)
from app.domain.scene_facts.schemas import SceneFactSet

OPTION_CAP = 20

#: contextual 匹配所用的 descriptor 字段（顺序即信号遍历顺序）。
_DESCRIPTOR_FIELDS = ("role", "faction", "appearance", "public_summary")


@dataclass(frozen=True)
class CandidateScopeDiagnostics:
    participant_exact: int
    text_exact: int
    #: 因多个档案共享同一 normalized variant 而退出 exact 的不同 variant 数。
    ambiguous_variants: int
    #: 受歧义 variant 影响、只能进 identity options 的档案数。
    ambiguous_profiles: int
    contextual_found: int
    contextual_included: int
    #: 因 cap 被裁掉的 non-exact（ambiguous + contextual）档案总数。
    overflow: int
    option_cap: int


@dataclass(frozen=True)
class CandidateScope:
    exact_targets: list[NPCIdentityOption]
    identity_options: list[NPCIdentityOption]
    diagnostics: CandidateScopeDiagnostics


def build_candidate_scope(
    *,
    scene_facts: str,
    current_state: CurrentState,
    profiles: list[NPCProfile],
    paragraph_texts: list[str] | None = None,
    option_cap: int = OPTION_CAP,
) -> CandidateScope:
    """从全部档案构建有界、确定性的身份候选池。

    ``scene_facts`` 必须是有效 ``SceneFactSet`` JSON；``paragraph_texts`` 为本轮
    scene paragraph evidence 的正文文本（“原文”），缺失时仅按 Scene Facts 匹配。

    ``option_cap`` 必须落在 ``[1, MAX_IDENTITY_OPTIONS]``，与 ``NPCTurnRequest``
    的 identity_options 上限共用同一事实源，保证 scope 输出恒可构造合法 request；
    exact 命中超过该上限时 fail-fast（不静默裁剪 exact）。
    """
    if not (1 <= option_cap <= MAX_IDENTITY_OPTIONS):
        raise ValueError(
            f"option_cap must be in [1, {MAX_IDENTITY_OPTIONS}], got {option_cap}"
        )
    parsed = SceneFactSet.model_validate_json(scene_facts)
    segments = [
        normalize_compact_key(text)
        for text in [
            parsed.scene_focus,
            *(fact.fact for fact in parsed.facts),
            *(paragraph_texts or []),
        ]
        if text
    ]
    player = normalize_compact_key(current_state.player.name)

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

    exact_surfaces: dict[str, set[str]] = {}
    participant_exact_ids: set[str] = set()
    ambiguous_variants: set[str] = set()

    for name in current_state.scene.participant_names:
        key = normalize_compact_key(name)
        if not key or key == player:
            continue
        ids = variant_index.get(key, [])
        if len(ids) == 1:
            npc_id = ids[0]
            exact_surfaces.setdefault(npc_id, set()).add(name)
            participant_exact_ids.add(npc_id)
        elif len(ids) > 1:
            ambiguous_variants.add(key)

    for profile in _profiles_sorted(profiles):
        if profile.npc_id in player_profiles:
            continue
        for variant in _profile_variants(profile):
            compact = normalize_compact_key(variant)
            if not eligible_highlight_term(compact):
                continue
            if not any(compact in segment for segment in segments):
                continue
            ids = variant_index.get(compact, [])
            if len(ids) == 1:
                exact_surfaces.setdefault(ids[0], set()).add(variant)
            else:
                ambiguous_variants.add(compact)

    exact_ids = sorted(
        exact_surfaces.keys(),
        key=lambda npc_id: (
            normalize_compact_key(by_id[npc_id].name),
            npc_id,
        ),
    )
    if len(exact_ids) > MAX_IDENTITY_OPTIONS:
        raise ValueError(
            f"exact targets exceed the identity option ceiling "
            f"({len(exact_ids)} > {MAX_IDENTITY_OPTIONS})"
        )

    ambiguous_ids: set[str] = set()
    for key in ambiguous_variants:
        ambiguous_ids.update(variant_index.get(key, []))
    ambiguous_ids.difference_update(exact_surfaces)
    ambiguous = sorted(
        (by_id[npc_id] for npc_id in ambiguous_ids),
        key=lambda profile: (
            normalize_compact_key(profile.name),
            profile.npc_id,
        ),
    )

    included_ids = set(exact_surfaces) | ambiguous_ids
    contextual = [
        profile
        for profile in profiles
        if profile.npc_id not in included_ids
        and profile.npc_id not in player_profiles
        and _signal_count(profile, segments) > 0
    ]
    contextual = sorted(
        contextual,
        key=lambda profile: (
            -_signal_count(profile, segments),
            -_longest_match(profile, segments),
            normalize_compact_key(profile.name),
            profile.npc_id,
        ),
    )

    remaining = max(0, option_cap - len(exact_ids))
    ambiguous_included = min(len(ambiguous), remaining)
    contextual_remaining = remaining - ambiguous_included
    contextual_included = min(len(contextual), contextual_remaining)

    options = [identity_option_from_profile(by_id[npc_id]) for npc_id in exact_ids]
    options.extend(
        identity_option_from_profile(profile) for profile in ambiguous[:ambiguous_included]
    )
    options.extend(
        identity_option_from_profile(profile) for profile in contextual[:contextual_included]
    )

    return CandidateScope(
        exact_targets=[identity_option_from_profile(by_id[npc_id]) for npc_id in exact_ids],
        identity_options=options,
        diagnostics=CandidateScopeDiagnostics(
            participant_exact=len(participant_exact_ids),
            text_exact=len(exact_ids) - len(participant_exact_ids),
            ambiguous_variants=len(ambiguous_variants),
            ambiguous_profiles=len(ambiguous_ids),
            contextual_found=len(contextual),
            contextual_included=contextual_included,
            overflow=(
                len(ambiguous)
                + len(contextual)
                - ambiguous_included
                - contextual_included
            ),
            option_cap=option_cap,
        ),
    )


def _build_variant_index(
    profiles: list[NPCProfile],
    player_profiles: set[str],
) -> dict[str, list[str]]:
    """``normalize_compact_key(variant) -> sorted npc_ids``，排除玩家档案并去重排序。"""
    variant_index: dict[str, set[str]] = {}
    for profile in profiles:
        if profile.npc_id in player_profiles:
            continue
        for variant in _profile_variants(profile):
            key = normalize_compact_key(variant)
            if not key:
                continue
            variant_index.setdefault(key, set()).add(profile.npc_id)
    return {
        key: sorted(ids)
        for key, ids in variant_index.items()
    }


def _profiles_sorted(profiles: list[NPCProfile]) -> list[NPCProfile]:
    return sorted(
        profiles,
        key=lambda profile: (normalize_compact_key(profile.name), profile.npc_id),
    )


def _profile_variants(profile: NPCProfile) -> list[str]:
    return [profile.name, *profile.aliases, *profile.transient_aliases.keys()]


def _descriptor_values(profile: NPCProfile) -> list[str]:
    return [getattr(profile, field) or "" for field in _DESCRIPTOR_FIELDS]


def _signal_count(profile: NPCProfile, segments: list[str]) -> int:
    return sum(
        1
        for value in _descriptor_values(profile)
        if _descriptor_matches(value, segments)
    )


def _longest_match(profile: NPCProfile, segments: list[str]) -> int:
    return max(
        (len(normalize_compact_key(value)) for value in _descriptor_values(profile)
         if _descriptor_matches(value, segments)),
        default=0,
    )


def _descriptor_matches(value: str, segments: list[str]) -> bool:
    compact = normalize_compact_key(value)
    if not eligible_highlight_term(compact):
        return False
    return any(compact in segment for segment in segments)


def identity_option_from_profile(profile: NPCProfile) -> NPCIdentityOption:
    """把档案投影为 ``NPCIdentityOption``（Ticket 02 的身份投影契约）。

    供候选池构造与 change admission 的投影一致性校验复用：aliases 合并永久 alias
    与未过期 transient alias 并做规范化去重。
    """
    return NPCIdentityOption(
        npc_id=profile.npc_id,
        name=profile.name,
        aliases=_option_aliases(profile),
        role=profile.role,
        public_summary=profile.public_summary,
        appearance=profile.appearance,
        faction=profile.faction,
        last_known_location=profile.last_known_location,
    )


def _option_aliases(profile: NPCProfile) -> list[str]:
    """永久 alias 保持原顺序，追加按规范化键排序的 transient aliases，并规范化去重。"""
    aliases: list[str] = []
    seen: set[str] = set()
    permanent = [alias for alias in profile.aliases]
    transient = sorted(profile.transient_aliases.keys(), key=normalize_compact_key)
    for alias in [*permanent, *transient]:
        key = normalize_compact_key(alias)
        if not key or key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases
