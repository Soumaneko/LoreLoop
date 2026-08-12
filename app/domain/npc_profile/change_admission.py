"""NPC change admission 与应用（Ticket 03）。

把 proposal 的合法性、evidence 门禁、no-op、字段规则、CurrentState 同步和
snapshot 应用收敛为一个深的 NPC change admission 模块：

- 先对整批 proposal 做硬校验（``validate_npc_turn_proposals``），任一错误即抛
  ``NPCTurnValidationError``，绝不产生部分结果；
- proposal pass 按顺序产出 accepted field/alias changes 与新 NPC draft 候选；
- CurrentState 同步 pass 对在场参与者确定性同步 location/attitude
  （proposal 写者被 schema 排除），多档案命中 fail-closed；
- application pass 让 snapshot 只由 accepted changes（字段 + alias）派生，
  绝不在 workflow 中重新推导变化。

确定性约定：

- 固定 ``admitted_at`` 入参，内部不调用随机 ID 或时钟；
- 新候选使用确定性 ``draft:<normalize_compact_key(name)>`` 占位 id（未经历
  Director enrich，不可持久化为主键）；
- 全部排序显式给出，同输入重放产生字节相等的结果。

明确限制（见 ticket 03 Comments）：

- 仅同步非 None 的 ``attitude_to_player``；「显式清空」暂不支持，绝不写回 None；
- 证据门禁只保证文档来源（id / source_type / turn / scene_id / request 正文一致），
  不保证段落↔NPC 语义归因（由 prompt 保证）；
- 已离场 NPC 的 ``last_known_location`` 不可由 proposal 更新
  （``departed_direction`` 能力为 npcturn 后端已知 gap，ticket 04/06 处理）。
"""

from __future__ import annotations

from typing import Iterable

from app.core.text import normalize_compact_key
from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.changes import (
    NPCTurnAdmissionError,
    NPCTurnValidationError,
    NPCBatchCoverage,
    AcceptedNPCAbilityAddChange,
    AcceptedNPCAliasChange,
    AcceptedNPCFieldChange,
    AcceptedNPCScalarChange,
    NPCAdmissionCoverage,
    NPCChangeAdmissionResult,
    NPCChangeAdmissionWarning,
)
from app.domain.npc_profile.schemas import (
    KnownAbility,
    NPCProfile,
    profile_from_update,
)
from app.domain.npc_profile.turn_contract import (
    AbilityAddChange,
    AliasMatchProposal,
    ExistingNPCProposal,
    NewNPCProposal,
    NPCBatchUpdateEntry,
    NPCTurnProposal,
    NPCTurnRequest,
    PublicSummaryChange,
    ScalarStableChange,
    validate_npc_turn_proposals,
)

_STABLE_PROPOSAL_FIELDS = frozenset(
    {"public_summary", "role", "appearance", "personality_trait", "faction"}
)


def admit_npc_turn_changes(
    *,
    request: NPCTurnRequest,
    proposals: list[NPCTurnProposal],
    profiles: list[NPCProfile],
    evidence_documents: dict[str, HistoryDocument],
    current_state: CurrentState,
    turn_index: int,
    scene_id: str,
    admitted_at: str,
) -> NPCChangeAdmissionResult:
    """把整批 proposal 审理为 accepted changes，并派生出更新后的档案快照。"""
    errors = validate_npc_turn_proposals(request, proposals)
    if errors:
        raise NPCTurnValidationError(errors)
    if scene_id != current_state.scene.scene_id:
        raise ValueError(
            f"scene_id mismatch: admission scene {scene_id!r} != "
            f"current_state scene {current_state.scene.scene_id!r}"
        )

    profile_by_id = _profile_index(profiles)
    if len(profile_by_id) != len(profiles):
        raise ValueError("profiles contain duplicate npc_ids")

    _verify_identity_projection(request, profile_by_id)

    valid_evidence = _valid_evidence(
        request,
        evidence_documents,
        turn_index=turn_index,
        scene_id=scene_id,
    )

    accepted_changes: list[AcceptedNPCFieldChange] = []
    accepted_alias_changes: list[AcceptedNPCAliasChange] = []
    new_candidates: list[NPCProfile] = []
    warnings: list[NPCChangeAdmissionWarning] = []
    added_ability_keys: dict[str, set[str]] = {}
    seen_current_ids: set[str] = set()
    pending_aliases: list[tuple[NPCProfile, str]] = []

    resolution_counts = {"existing": 0, "alias_match": 0, "new": 0, "ignore": 0}

    for proposal in proposals:
        if isinstance(proposal, (ExistingNPCProposal, AliasMatchProposal)):
            target = profile_by_id.get(proposal.target_npc_id)
            if target is None:
                raise ValueError(
                    f"proposal target {proposal.target_npc_id!r} is not in profiles"
                )
            _admit_current_patch(
                target,
                proposal,
                turn_index,
                accepted_changes,
                seen_current_ids,
            )
            for change in proposal.stable_changes:
                _admit_stable_change(
                    target,
                    change,
                    valid_evidence,
                    turn_index,
                    accepted_changes,
                    warnings,
                    added_ability_keys,
                )
            if (
                isinstance(proposal, AliasMatchProposal)
                and proposal.persist_alias
            ):
                pending_aliases.append((target, proposal.surface))
            resolution_counts[proposal.resolution] += 1
        elif isinstance(proposal, NewNPCProposal):
            new_candidates.append(
                _candidate_from_new(proposal, scene_id, admitted_at)
            )
            resolution_counts["new"] += 1
        else:
            resolution_counts["ignore"] += 1

    # 候选全局身份协调：canonical name 冲突整批拒、alias 冲突剥离并告警，
    # 保证后续 alias persistence 与参与者同步在干净的身份空间上进行。
    new_candidates, identity_warnings = _reconcile_candidate_identities(
        profiles,
        new_candidates,
    )
    warnings.extend(identity_warnings)

    # alias 在整个候选集产生后再处理，避免 proposal 顺序影响冲突判定。
    for target, surface in pending_aliases:
        _admit_alias(
            target,
            surface,
            profiles,
            new_candidates,
            turn_index,
            accepted_alias_changes,
            warnings,
        )

    synced_ids = _sync_participants(
        profiles,
        new_candidates,
        current_state,
        turn_index,
        accepted_changes,
    )

    updated_profiles = _apply_accepted_changes(
        profiles,
        accepted_changes,
        accepted_alias_changes,
        scene_id,
        admitted_at,
    )

    warnings = sorted(
        warnings,
        key=lambda warning: (
            warning.npc_name,
            warning.field_name,
            warning.code,
            warning.evidence_ids,
        ),
    )

    unresolved_surfaces = sorted({
        proposal.surface
        for proposal in proposals
        if isinstance(proposal, AliasMatchProposal)
    })

    return NPCChangeAdmissionResult(
        accepted_profile_changes=accepted_changes,
        accepted_alias_changes=accepted_alias_changes,
        new_npc_candidates=sorted(
            new_candidates,
            key=lambda profile: (
                normalize_compact_key(profile.name),
                profile.npc_id,
            ),
        ),
        warnings=warnings,
        coverage=NPCAdmissionCoverage(
            exact_targets=len(request.exact_targets),
            existing_proposals=resolution_counts["existing"],
            alias_proposals=resolution_counts["alias_match"],
            new_proposals=resolution_counts["new"],
            ignore_proposals=resolution_counts["ignore"],
            unresolved_surfaces=unresolved_surfaces,
            synced_npcs=len(synced_ids),
        ),
        updated_profiles=updated_profiles,
    )


def admit_npc_batch_update(
    *,
    updates: list[NPCBatchUpdateEntry],
    entry_warnings: list[NPCChangeAdmissionWarning] | None = None,
    resolved_npc_ids: Iterable[str],
    alias_changes: list[AcceptedNPCAliasChange],
    profiles: list[NPCProfile],
    evidence_documents: dict[str, HistoryDocument],
    current_state: CurrentState,
    turn_index: int,
    scene_id: str,
    admitted_at: str,
) -> NPCChangeAdmissionResult:
    """审理 NPC batch field updater 的输出，per-field 容错（宁漏勿错）。

    只有程序错误才整批失败：scene_id 不匹配、profiles 重复 npc_id、解析出的
    npc_id 没有对应档案。模型输出层面的问题一律只丢对应条目 / 字段：

    - schema 解析失败的条目 → 丢弃并记 warning（``invalid_update_entry``，由
      ``entry_warnings`` 传入，宽松 wire envelope 阶段产生）；
    - 未知或未解析 npc_id 的条目 → 丢弃并记 warning（``unknown_npc_id`` /
      ``unresolved_npc_id``）；
    - 字段证据非法 → 只丢该字段（``change_evidence_dropped``）；
    - 同一 (npc_id, field) 重复（能力按规范化名称去重）→ 保留接受的首条；
    - 已解析但无输出的 NPC → no-op；
    - CurrentState 同步独立执行，不受 LLM 输出影响。
    """
    if scene_id != current_state.scene.scene_id:
        raise ValueError(
            f"scene_id mismatch: admission scene {scene_id!r} != "
            f"current_state scene {current_state.scene.scene_id!r}"
        )

    profile_by_id = _profile_index(profiles)
    if len(profile_by_id) != len(profiles):
        raise ValueError("profiles contain duplicate npc_ids")

    resolved_ids = set(resolved_npc_ids)
    if resolved_ids - set(profile_by_id):
        raise ValueError(
            "resolved npc_ids have no profile: "
            f"{sorted(resolved_ids - set(profile_by_id))}"
        )

    alias_npc_ids = {change.npc_id for change in alias_changes}
    if alias_npc_ids - resolved_ids:
        raise ValueError(
            "alias changes reference npc_ids outside the resolved set: "
            f"{sorted(alias_npc_ids - resolved_ids)}"
        )

    # alias mutation 自身校验（确定性输入损坏 → ValueError）：
    # canonical_name 必须与档案一致、alias 不得为空白。
    for change in alias_changes:
        profile = profile_by_id[change.npc_id]
        if change.canonical_name != profile.name:
            raise ValueError(
                f"alias change canonical_name {change.canonical_name!r} "
                f"does not match profile name {profile.name!r}"
            )
        if not normalize_compact_key(change.alias):
            raise ValueError(
                f"alias change for {profile.npc_id!r} has a blank alias"
            )

    # 已是目标档案 name / 既有 alias / transient alias 的 alias → no-op 过滤
    # （不写入、不计入 updated）。
    alias_changes = [
        change
        for change in alias_changes
        if not _alias_already_known(profile_by_id[change.npc_id], change.alias)
    ]

    valid_evidence = _batch_valid_evidence(
        evidence_documents,
        turn_index=turn_index,
        scene_id=scene_id,
    )

    accepted_changes: list[AcceptedNPCFieldChange] = []
    warnings: list[NPCChangeAdmissionWarning] = list(entry_warnings or [])
    added_ability_keys: dict[str, set[str]] = {}
    seen_current_ids: set[str] = set()
    seen_stable_fields: set[tuple[str, str]] = set()
    # 只有真正被丢弃的 entry（schema 阶段 invalid_update_entry）计入；
    # 字段级 warning（current_intent / stable change）条目仍然存活。
    dropped_entries = sum(
        1
        for warning in (entry_warnings or [])
        if warning.code == "invalid_update_entry"
    )

    for entry in updates:
        target = profile_by_id.get(entry.npc_id)
        if target is None:
            dropped_entries += 1
            warnings.append(NPCChangeAdmissionWarning(
                npc_name=entry.npc_id,
                field_name="npc_id",
                code="unknown_npc_id",
                message=(
                    f"batch update references unknown npc_id "
                    f"{entry.npc_id!r}; entry dropped"
                ),
            ))
            continue
        if entry.npc_id not in resolved_ids:
            dropped_entries += 1
            warnings.append(NPCChangeAdmissionWarning(
                npc_name=target.name,
                field_name="npc_id",
                code="unresolved_npc_id",
                message=(
                    f"batch update references npc {entry.npc_id!r} that was not "
                    "resolved this turn; entry dropped"
                ),
            ))
            continue
        _admit_batch_current_intent(
            target,
            entry.current_intent,
            turn_index,
            accepted_changes,
            seen_current_ids,
        )
        for change in entry.stable_changes:
            if isinstance(change, AbilityAddChange):
                # append-only 能力按规范化名称去重（``added_ability_keys`` 内部处理），
                # 不参与标量字段的 (npc_id, field) 去重。
                _admit_stable_change(
                    target,
                    change,
                    valid_evidence,
                    turn_index,
                    accepted_changes,
                    warnings,
                    added_ability_keys,
                )
                continue
            field_key = (entry.npc_id, change.field)
            if field_key in seen_stable_fields:
                warnings.append(NPCChangeAdmissionWarning(
                    npc_name=target.name,
                    field_name=change.field,
                    code="duplicate_stable_field",
                    message=(
                        f"stable field {change.field!r} is updated more than once "
                        f"for npc {entry.npc_id!r}; duplicate dropped"
                    ),
                ))
                continue
            accepted = _admit_stable_change(
                target,
                change,
                valid_evidence,
                turn_index,
                accepted_changes,
                warnings,
                added_ability_keys,
            )
            if accepted:
                seen_stable_fields.add(field_key)

    synced_ids = _sync_participants(
        profiles,
        [],
        current_state,
        turn_index,
        accepted_changes,
    )

    updated_profiles = _apply_accepted_changes(
        profiles,
        accepted_changes,
        alias_changes,
        scene_id,
        admitted_at,
    )

    updated_npc_ids = {change.npc_id for change in accepted_changes}
    # 已接受的确定性 alias 同样写入档案（``_apply_accepted_changes`` 应用），
    # 因此 alias-only 的 npc 计入 updated、排除出 no-op。
    updated_npc_ids.update(change.npc_id for change in alias_changes)
    warnings = sorted(
        warnings,
        key=lambda warning: (
            warning.npc_name,
            warning.field_name,
            warning.code,
            warning.evidence_ids,
        ),
    )

    return NPCChangeAdmissionResult(
        accepted_profile_changes=accepted_changes,
        accepted_alias_changes=list(alias_changes),
        warnings=warnings,
        coverage=NPCBatchCoverage(
            resolved_npcs=len(resolved_ids),
            updated_npcs=len(updated_npc_ids),
            no_op_npcs=len(resolved_ids - updated_npc_ids),
            dropped_entries=dropped_entries,
            synced_npcs=len(synced_ids),
        ),
        updated_profiles=updated_profiles,
    )


def _alias_already_known(profile: NPCProfile, alias: str) -> bool:
    """alias 已是目标档案的 name / 永久 alias / transient alias → no-op。"""
    key = normalize_compact_key(alias)
    if not key:
        return True
    return key == normalize_compact_key(profile.name) or key in {
        normalize_compact_key(value)
        for value in [*profile.aliases, *profile.transient_aliases]
    }


def _batch_valid_evidence(
    evidence_documents: dict[str, HistoryDocument],
    *,
    turn_index: int,
    scene_id: str,
) -> dict[str, HistoryDocument]:
    """batch 版本的证据门禁：只需 id 解析为本轮本场景的正文段落。"""
    return {
        document.document_id: document
        for mapping_key, document in evidence_documents.items()
        if mapping_key == document.document_id
        and document.source_type == "scene_paragraph"
        and document.turn_index == turn_index
        and document.scene_id == scene_id
    }


def _admit_batch_current_intent(
    target: NPCProfile,
    value: str | None,
    turn_index: int,
    accepted_changes: list[AcceptedNPCFieldChange],
    seen_current_ids: set[str],
) -> None:
    if value is None or value == target.current_intent:
        return
    if target.npc_id in seen_current_ids:
        return
    seen_current_ids.add(target.npc_id)
    accepted_changes.append(AcceptedNPCScalarChange(
        npc_id=target.npc_id,
        canonical_name=target.name,
        field="current_intent",
        old_value=target.current_intent,
        new_value=value,
        turn_index=turn_index,
        source="proposal",
    ))


def _profile_index(profiles: list[NPCProfile]) -> dict[str, NPCProfile]:
    return {profile.npc_id: profile for profile in profiles}


def _verify_identity_projection(
    request: NPCTurnRequest,
    profile_by_id: dict[str, NPCProfile],
) -> None:
    """校验 request.identity_options 是实际 profiles 的忠实投影（整批 fail-closed）。

    每个身份选项都必须对应一个真实档案，且完整投影（name / aliases 含 transient /
    role / public_summary / appearance / faction / last_known_location）与档案一致。
    不一致意味着 request 过期、错误组装或恢复阶段档案漂移，会产生跨人物错误归因，
    因此在产生任何 accepted changes 之前拒绝整批。
    """
    from app.domain.npc_profile.candidate_scope import identity_option_from_profile

    for option in request.identity_options:
        profile = profile_by_id.get(option.npc_id)
        if profile is None:
            raise ValueError(
                f"identity option {option.npc_id!r} has no matching profile; "
                "refusing the whole batch"
            )
        expected = identity_option_from_profile(profile)
        if option.model_dump() != expected.model_dump():
            raise ValueError(
                f"identity option {option.npc_id!r} does not match the actual "
                "profile projection; refusing the whole batch"
            )


def _valid_evidence(
    request: NPCTurnRequest,
    evidence_documents: dict[str, HistoryDocument],
    *,
    turn_index: int,
    scene_id: str,
) -> dict[str, HistoryDocument]:
    """构造证据门禁集合：仅接受 request 与真实文档一致的、本轮本场景的正文段落。

    强制 ``mapping_key == document.document_id == request_ref.document_id``：
    字典键与文档自身 ID 不一致的条目直接视为无效，避免 provenance 指向不存在的
    文档。
    """
    request_refs = {ref.document_id: ref for ref in request.scene_paragraph_evidence}
    valid: dict[str, HistoryDocument] = {}
    for mapping_key, document in evidence_documents.items():
        if mapping_key != document.document_id:
            continue
        ref = request_refs.get(document.document_id)
        if document.source_type != "scene_paragraph":
            continue
        if document.turn_index != turn_index:
            continue
        if document.scene_id != scene_id:
            continue
        if ref is None or ref.text != document.text:
            continue
        valid[document.document_id] = document
    return valid


def _admit_current_patch(
    target: NPCProfile,
    proposal: ExistingNPCProposal | AliasMatchProposal,
    turn_index: int,
    accepted_changes: list[AcceptedNPCFieldChange],
    seen_current_ids: set[str],
) -> None:
    patch = proposal.current_patch
    if patch is None or patch.current_intent is None:
        return
    value = patch.current_intent
    if value == target.current_intent:
        return
    if target.npc_id in seen_current_ids:
        return
    seen_current_ids.add(target.npc_id)
    accepted_changes.append(AcceptedNPCScalarChange(
        npc_id=target.npc_id,
        canonical_name=target.name,
        field="current_intent",
        old_value=target.current_intent,
        new_value=value,
        turn_index=turn_index,
        source="proposal",
    ))


def _admit_stable_change(
    target: NPCProfile,
    change: AbilityAddChange | PublicSummaryChange | ScalarStableChange,
    valid_evidence: dict[str, HistoryDocument],
    turn_index: int,
    accepted_changes: list[AcceptedNPCFieldChange],
    warnings: list[NPCChangeAdmissionWarning],
    added_ability_keys: dict[str, set[str]],
) -> bool:
    """per-field 容错审理单条稳定字段变化；返回是否实际接受（append 了 change）。"""
    invalid = [
        evidence_id
        for evidence_id in change.evidence_ids
        if evidence_id not in valid_evidence
    ]
    if invalid:
        warnings.append(NPCChangeAdmissionWarning(
            npc_name=target.name,
            field_name=change.field,
            code="change_evidence_dropped",
            message=(
                "stable field change dropped: evidence ids are not confirmed "
                "scene paragraphs of this turn in this scene"
            ),
            evidence_ids=tuple(sorted(set(invalid))),
        ))
        return False

    if isinstance(change, AbilityAddChange):
        return _admit_ability_add(
            target,
            change,
            turn_index,
            accepted_changes,
            added_ability_keys,
        )

    if isinstance(change, PublicSummaryChange):
        if change.value == target.public_summary:
            return False
        accepted_changes.append(AcceptedNPCScalarChange(
            npc_id=target.npc_id,
            canonical_name=target.name,
            field="public_summary",
            old_value=target.public_summary,
            new_value=change.value,
            evidence_ids=tuple(change.evidence_ids),
            turn_index=turn_index,
            summary_delta=change.summary_delta,
            source="proposal",
        ))
        return True

    current = getattr(target, change.field)
    if change.value == current:
        return False
    accepted_changes.append(AcceptedNPCScalarChange(
        npc_id=target.npc_id,
        canonical_name=target.name,
        field=change.field,
        old_value=current,
        new_value=change.value,
        evidence_ids=tuple(change.evidence_ids),
        turn_index=turn_index,
        source="proposal",
    ))
    return True


def _admit_ability_add(
    target: NPCProfile,
    change: AbilityAddChange,
    turn_index: int,
    accepted_changes: list[AcceptedNPCFieldChange],
    added_ability_keys: dict[str, set[str]],
) -> bool:
    """append-only 能力 add：按规范化能力名去重、上限 5；返回是否实际接受。"""
    key = normalize_compact_key(change.ability.name)
    if not key:
        return False
    existing_keys = {
        normalize_compact_key(ability.name)
        for ability in target.known_abilities
    }
    added = added_ability_keys.setdefault(target.npc_id, set())
    if key in existing_keys or key in added:
        return False
    if len(target.known_abilities) + len(added) >= 5:
        return False
    added.add(key)
    accepted_changes.append(AcceptedNPCAbilityAddChange(
        npc_id=target.npc_id,
        canonical_name=target.name,
        additions=[
            KnownAbility(
                name=change.ability.name,
                description=change.ability.description,
            )
        ],
        evidence_ids=tuple(change.evidence_ids),
        turn_index=turn_index,
        source="proposal",
    ))
    return True


def _admit_alias(
    target: NPCProfile,
    surface: str,
    profiles: list[NPCProfile],
    new_candidates: list[NPCProfile],
    turn_index: int,
    accepted_alias_changes: list[AcceptedNPCAliasChange],
    warnings: list[NPCChangeAdmissionWarning],
) -> None:
    key = normalize_compact_key(surface)
    if not key:
        return
    target_keys = {
        normalize_compact_key(value)
        for value in [target.name, *target.aliases, *target.transient_aliases]
    }
    if key in target_keys:
        return
    for other in [*profiles, *new_candidates]:
        if other.npc_id == target.npc_id:
            continue
        other_keys = {
            normalize_compact_key(value)
            for value in [other.name, *other.aliases, *other.transient_aliases]
        }
        if key in other_keys:
            warnings.append(NPCChangeAdmissionWarning(
                npc_name=target.name,
                field_name="aliases",
                code="alias_conflicts_with_other_npc",
                message=(
                    "alias persisted for another npc would collide with an "
                    "existing identity variant"
                ),
            ))
            return
    accepted_alias_changes.append(AcceptedNPCAliasChange(
        npc_id=target.npc_id,
        canonical_name=target.name,
        alias=surface,
        turn_index=turn_index,
    ))


def _candidate_from_new(
    proposal: NewNPCProposal,
    scene_id: str,
    admitted_at: str,
) -> NPCProfile:
    """创建新 NPC draft：忽略模型填写的 location/attitude（CurrentState 独占）。"""
    update = proposal.new_profile.to_update_input().model_copy(update={
        "last_known_location": None,
        "attitude_to_player": None,
    })
    draft_id = f"draft:{normalize_compact_key(proposal.new_profile.name)}"
    return profile_from_update(
        update,
        npc_id=draft_id,
        source_scene_ids=[scene_id],
        updated_at=admitted_at,
    )


def _reconcile_candidate_identities(
    profiles: list[NPCProfile],
    candidates: list[NPCProfile],
) -> tuple[list[NPCProfile], list[NPCChangeAdmissionWarning]]:
    """对全部新候选做全局身份 variant 协调。

    - canonical name 与既有档案任意身份 variant（name/alias/transient）冲突 →
      整批 fail-closed（候选间 name 由 surface 唯一保证，不会互相冲突）；
    - candidate alias 与任何其它身份 variant（既有档案或其它候选）冲突 → 剥离该
      alias 并产生 warning（new↔existing、new↔new 全覆盖）。

    候选 name 永不修改、候选间 name 唯一，因此规则与候选处理顺序无关。
    """
    index: dict[str, dict[str, NPCProfile]] = {}
    for identity in [*profiles, *candidates]:
        for variant in [identity.name, *identity.aliases, *identity.transient_aliases]:
            key = normalize_compact_key(variant)
            if key:
                index.setdefault(key, {})[identity.npc_id] = identity

    warnings: list[NPCChangeAdmissionWarning] = []
    reconciled: list[NPCProfile] = []
    for candidate in candidates:
        name_key = normalize_compact_key(candidate.name)
        if name_key and len(index.get(name_key, {})) > 1:
            raise NPCTurnAdmissionError(
                f"new candidate name {candidate.name!r} collides with an "
                "existing identity variant; refusing the whole batch"
            )
        kept_aliases: list[str] = []
        seen_alias_keys: set[str] = set()
        for alias in candidate.aliases:
            key = normalize_compact_key(alias)
            if not key or key == name_key:
                continue
            if key in seen_alias_keys:
                continue
            seen_alias_keys.add(key)
            if len(index.get(key, {})) > 1:
                warnings.append(NPCChangeAdmissionWarning(
                    npc_name=candidate.name,
                    field_name="aliases",
                    code="candidate_alias_conflict_stripped",
                    message=(
                        "new candidate alias stripped: it collides with an "
                        "existing identity variant"
                    ),
                ))
                continue
            kept_aliases.append(alias)
        reconciled.append(candidate.model_copy(update={"aliases": kept_aliases}))
    return reconciled, warnings



def _resolve_participants(
    current_state: CurrentState,
    identities: list[NPCProfile],
) -> list[tuple[object, str]]:
    """把参与者确定性映射到身份（name/永久 alias/transient alias，fail-closed）。

    在 ``profiles + new_candidates`` 的统一身份空间上解析（按 npc_id 去重）：
    参与者名命中唯一 npc_id 才返回；命中多个不同 npc_id（跨 NPC 冲突）或命中玩家
    时跳过，避免把当前场景信息写到错误档案或多个 NPC。返回 ``[(participant, npc_id)]``。
    """
    player = normalize_compact_key(current_state.player.name)
    index: dict[str, dict[str, NPCProfile]] = {}
    for profile in identities:
        if any(
            normalize_compact_key(value) == player
            for value in [profile.name, *profile.aliases, *profile.transient_aliases]
        ):
            continue
        for value in [profile.name, *profile.aliases, *profile.transient_aliases]:
            key = normalize_compact_key(value)
            if key:
                index.setdefault(key, {})[profile.npc_id] = profile

    resolved: list[tuple[object, str]] = []
    for participant in current_state.scene.participants:
        key = normalize_compact_key(participant.name)
        if not key or key == player:
            continue
        matches = index.get(key, {})
        if len(matches) != 1:
            continue
        resolved.append((participant, next(iter(matches))))
    return resolved


def _sync_participants(
    profiles: list[NPCProfile],
    candidates: list[NPCProfile],
    current_state: CurrentState,
    turn_index: int,
    accepted_changes: list[AcceptedNPCFieldChange],
) -> set[str]:
    """在统一身份空间上同步在场参与者：既有档案产 accepted change，候选同步到自身。

    按 npc_id 聚合：同一 NPC 命中多个 participant 时，相同字段值去重为单条；
    不同 attitude 值视为 CurrentState 自相矛盾，fail-closed（整批拒绝）。
    """
    profile_by_id = _profile_index(profiles)
    candidate_by_id = _profile_index(candidates)
    resolved = _resolve_participants(
        current_state,
        [*profiles, *candidates],
    )
    by_npc: dict[str, list[object]] = {}
    for participant, npc_id in resolved:
        by_npc.setdefault(npc_id, []).append(participant)

    synced_ids: set[str] = set()
    for npc_id in sorted(by_npc):
        profile = profile_by_id.get(npc_id) or candidate_by_id[npc_id]
        changes = _sync_profile_aggregated(
            profile,
            by_npc[npc_id],
            current_state,
            turn_index,
        )
        accepted_changes.extend(changes)
        if npc_id in candidate_by_id:
            for change in changes:
                setattr(profile, change.field, change.new_value)
        if changes:
            synced_ids.add(npc_id)
    return synced_ids


def _sync_profile_aggregated(
    profile: NPCProfile,
    participants: list[object],
    current_state: CurrentState,
    turn_index: int,
) -> list[AcceptedNPCScalarChange]:
    """对单个在场档案确定性同步 location/attitude，只产出 accepted changes。

    ``participants`` 为解析到同一 npc_id 的全部参与者：location 恒为单一 scene
    location，天然去重；attitude 多个取值去重后若仍冲突则整批拒绝。
    """
    changes: list[AcceptedNPCScalarChange] = []
    scene_location = current_state.scene.location.strip()
    if scene_location and scene_location != profile.last_known_location:
        changes.append(AcceptedNPCScalarChange(
            npc_id=profile.npc_id,
            canonical_name=profile.name,
            field="last_known_location",
            old_value=profile.last_known_location,
            new_value=scene_location,
            turn_index=turn_index,
            source="current_state_sync",
        ))

    attitudes: set[str] = set()
    for participant in participants:
        raw = getattr(participant, "attitude_to_player", None)
        if isinstance(raw, str) and raw.strip():
            attitudes.add(raw.strip())
    if len(attitudes) > 1:
        raise NPCTurnAdmissionError(
            f"conflicting participant attitudes for npc {profile.npc_id!r}: "
            f"{sorted(attitudes)}"
        )
    attitude = next(iter(attitudes)) if attitudes else None
    if attitude is not None and attitude != profile.attitude_to_player:
        changes.append(AcceptedNPCScalarChange(
            npc_id=profile.npc_id,
            canonical_name=profile.name,
            field="attitude_to_player",
            old_value=profile.attitude_to_player,
            new_value=attitude,
            turn_index=turn_index,
            source="current_state_sync",
        ))
    return changes


def _apply_accepted_changes(
    profiles: list[NPCProfile],
    accepted_changes: list[AcceptedNPCFieldChange],
    accepted_alias_changes: list[AcceptedNPCAliasChange],
    scene_id: str,
    admitted_at: str,
) -> list[NPCProfile]:
    """snapshot 只由 accepted mutations 派生；draft 候选由调用方单独处理。"""
    profile_by_id = _profile_index(profiles)
    mutated: dict[str, NPCProfile] = {}

    for change in accepted_changes:
        if change.npc_id not in profile_by_id:
            continue
        profile = mutated.get(change.npc_id) or profile_by_id[change.npc_id].model_copy(
            deep=True
        )
        if isinstance(change, AcceptedNPCScalarChange):
            setattr(profile, change.field, change.new_value)
        else:
            profile.known_abilities = [
                *profile.known_abilities,
                *change.additions,
            ]
        _touch_profile(profile, scene_id, admitted_at)
        mutated[profile.npc_id] = profile

    for alias in accepted_alias_changes:
        if alias.npc_id not in profile_by_id:
            continue
        profile = mutated.get(alias.npc_id) or profile_by_id[alias.npc_id].model_copy(
            deep=True
        )
        profile.aliases = [*profile.aliases, alias.alias]
        _touch_profile(profile, scene_id, admitted_at)
        mutated[profile.npc_id] = profile

    return sorted(
        mutated.values(),
        key=lambda profile: (normalize_compact_key(profile.name), profile.npc_id),
    )


def _touch_profile(profile: NPCProfile, scene_id: str, admitted_at: str) -> None:
    if scene_id not in profile.source_scene_ids:
        profile.source_scene_ids = [*profile.source_scene_ids, scene_id]
    profile.updated_at = admitted_at
