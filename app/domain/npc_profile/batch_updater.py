"""NPC batch field updater（Ticket 08，宁漏勿错）。

确定性身份解析（``identity_resolver``）已把「称呼→档案」归并完成，本模块只做两件事：

1. 单次 LLM 调用：把每个已解析 NPC 的当前档案 + 本章对应 facts/evidence 投影成
   ``npc_batch_json``，让 LLM 只输出 ``npc_id → field changes``（不再判断
   mention/alias/new/ignore，不再生成新 NPC 完整档案）；
2. 确定性 per-field 容错 admission：单条/单字段非法只丢那一项，CurrentState 同步
   独立执行，只有程序错误才整批失败。

宽松 wire envelope：``NPCBatchUpdate.updates`` 保持 ``list[dict]``，逐条
``parse_batch_updates`` 校验，schema 失败的条目只丢自己并记带 index 的 warning。

职责链：``apply_npc_batch_update`` = 统一 current_scene_evidence →
resolve_npc_identities → 单次调用 → per-entry 解析 → ``admit_npc_batch_update``
→ 应用到 store（upsert / alias / mention hit）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.core.text import normalize_compact_key
from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.change_admission import admit_npc_batch_update
from app.domain.npc_profile.changes import (
    NPCBatchCoverage,
    AcceptedNPCFieldChange,
    AcceptedNPCAliasChange,
    NPCChangeAdmissionResult,
    NPCChangeAdmissionWarning,
)
from app.domain.npc_profile.identity_resolver import (
    NpcCandidate,
    NpcIdentityResolution,
    ResolvedNpc,
    resolve_npc_identities,
    surface_is_known,
)
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.turn_contract import (
    NPCBatchUpdate,
    NPCBatchUpdateDisplay,
    NPCBatchUpdateEntry,
    NPCStableChange,
)
from app.domain.scene_facts.schemas import SceneFactSet

_SOURCE_SCENE_PARAGRAPH = "scene_paragraph"

#: 与 ``NPCBatchUpdateEntry.stable_changes`` 的 ``max_length`` 保持一致。
MAX_STABLE_CHANGES = 8

_STABLE_CHANGE_ADAPTER = TypeAdapter(NPCStableChange)


@dataclass(frozen=True)
class ParsedBatch:
    entries: list[NPCBatchUpdateEntry]
    entry_warnings: list[NPCChangeAdmissionWarning]


@dataclass
class NPCBatchProcessResult:
    saved_ids: list[str]
    new_npc_candidates: list[NpcCandidate]
    warnings: list[NPCChangeAdmissionWarning]
    accepted_changes: list[AcceptedNPCFieldChange]
    accepted_alias_changes: list[AcceptedNPCAliasChange]
    coverage: NPCBatchCoverage | None
    resolution: NpcIdentityResolution


class NPCBatchUpdater:
    """单次批量字段更新调用：请求投影 + gateway 调用 + 逐条解析。"""

    def __init__(self, gateway: LLMGateway):
        self._gateway = gateway

    def process(
        self,
        *,
        resolved: list[ResolvedNpc],
        profiles: list[NPCProfile],
        scene_facts: SceneFactSet,
        evidence_documents: dict[str, HistoryDocument],
        turn_index: int,
        scene_id: str,
        trace_id: str | None = None,
    ) -> ParsedBatch:
        if not resolved:
            return ParsedBatch(entries=[], entry_warnings=[])
        input_data = build_npc_batch_input(
            resolved=resolved,
            profiles=profiles,
            scene_facts=scene_facts,
            evidence_documents=evidence_documents,
        )
        input_data["output_schema_json"] = schema_json(NPCBatchUpdateDisplay)
        batch = cast(
            NPCBatchUpdate,
            self._gateway.run(
                task="npc_batch_update",
                input_data=input_data,
                output_schema=NPCBatchUpdate,
                trace_id=trace_id,
            ),
        )
        entries, entry_warnings = parse_batch_updates(batch.updates)
        return ParsedBatch(entries=entries, entry_warnings=entry_warnings)


def parse_batch_updates(
    raw_updates: list[Any],
) -> tuple[list[NPCBatchUpdateEntry], list[NPCChangeAdmissionWarning]]:
    """逐字段 salvage 宽松 wire envelope（宁漏勿错）。

    - 非对象元素 / 缺或非 str ``npc_id`` → 丢整条（无法归因）；
    - ``stable_changes`` 非列表 → 丢整字段；逐条 ``NPCStableChange.model_validate``，
      失败只丢该 change（``invalid_stable_change``，带 index）；
    - 合法 npc_id + 剩余合法字段照常构造 typed 条目进入 admission。
    """
    entries: list[NPCBatchUpdateEntry] = []
    warnings: list[NPCChangeAdmissionWarning] = []
    for index, raw in enumerate(raw_updates):
        if not isinstance(raw, dict):
            warnings.append(NPCChangeAdmissionWarning(
                npc_name=f"#{index}",
                field_name="entry",
                code="invalid_update_entry",
                message=(
                    f"batch update entry {index} is not an object; dropped"
                ),
            ))
            continue
        npc_id = raw.get("npc_id")
        if not isinstance(npc_id, str) or not npc_id.strip():
            warnings.append(NPCChangeAdmissionWarning(
                npc_name=str(npc_id or f"#{index}"),
                field_name="npc_id",
                code="invalid_update_entry",
                message=(
                    f"batch update entry {index} has no valid npc_id; dropped"
                ),
            ))
            continue
        npc_id = npc_id.strip()

        stable_changes: list[NPCStableChange] = []
        raw_changes = raw.get("stable_changes")
        if raw_changes is not None:
            if not isinstance(raw_changes, list):
                warnings.append(NPCChangeAdmissionWarning(
                    npc_name=npc_id,
                    field_name="stable_changes",
                    code="invalid_stable_changes",
                    message=(
                        f"batch update entry {index} stable_changes is not a "
                        "list; dropped"
                    ),
                ))
            else:
                for change_index, raw_change in enumerate(raw_changes):
                    try:
                        parsed_change = _STABLE_CHANGE_ADAPTER.validate_python(
                            raw_change
                        )
                    except ValidationError as exc:
                        warnings.append(NPCChangeAdmissionWarning(
                            npc_name=npc_id,
                            field_name="stable_changes",
                            code="invalid_stable_change",
                            message=(
                                f"batch update entry {index} change "
                                f"{change_index} failed schema validation; "
                                f"dropped: {exc}"
                            ),
                        ))
                        continue
                    if len(stable_changes) >= MAX_STABLE_CHANGES:
                        warnings.append(NPCChangeAdmissionWarning(
                            npc_name=npc_id,
                            field_name="stable_changes",
                            code="stable_change_limit_exceeded",
                            message=(
                                f"batch update entry {index} change "
                                f"{change_index} exceeds the "
                                f"{MAX_STABLE_CHANGES}-change cap; dropped"
                            ),
                        ))
                        continue
                    stable_changes.append(parsed_change)
        entries.append(NPCBatchUpdateEntry(
            npc_id=npc_id,
            stable_changes=stable_changes,
        ))
    return entries, warnings


def build_npc_batch_input(
    *,
    resolved: list[ResolvedNpc],
    profiles: list[NPCProfile],
    scene_facts: SceneFactSet,
    evidence_documents: dict[str, HistoryDocument],
) -> dict[str, str]:
    """确定性请求投影：每 npc 的当前公开字段 + 本章相关 facts + 相关正文 evidence。

    processor 与 benchmark 的 staleness 镜像共用同一事实源。调用方应传入已过滤为
    本轮本场景正文段落的 ``evidence_documents``。
    """
    by_id = {profile.npc_id: profile for profile in profiles}
    entries: list[dict] = []
    for item in resolved:
        profile = by_id.get(item.npc_id)
        if profile is None:
            continue
        identity_surfaces = [
            *item.primary_surfaces,
            *(relation.surface for relation in item.alias_relations),
        ]
        surface_keys = [
            normalize_compact_key(surface) for surface in identity_surfaces
        ]
        fact_evidence = set(item.evidence_fact_ids)
        paragraph_evidence = set(item.evidence_paragraph_ids)
        facts = [
            {"fact_id": fact.fact_id, "fact": fact.fact}
            for fact in scene_facts.facts
            if fact.fact_id in fact_evidence
            or any(key in normalize_compact_key(fact.fact) for key in surface_keys)
        ]
        evidence = [
            {"document_id": document.document_id, "text": document.text}
            for document in sorted(
                (
                    document
                    for document in evidence_documents.values()
                    if document.source_type == _SOURCE_SCENE_PARAGRAPH
                    and (
                        document.document_id in paragraph_evidence
                        or any(
                            key in normalize_compact_key(document.text)
                            for key in surface_keys
                        )
                    )
                ),
                key=lambda document: (
                    document.paragraph_index if document.paragraph_index is not None else 0,
                    document.document_id,
                ),
            )
        ]
        entries.append({
            "npc_id": item.npc_id,
            "name": item.canonical_name,
            "current": {
                "role": profile.role,
                "appearance": profile.appearance,
                "faction": profile.faction,
            },
            "facts": facts,
            "evidence": evidence,
        })
    return {"npc_batch_json": json.dumps(entries, ensure_ascii=False, indent=2)}


def _current_scene_evidence(
    evidence_documents: dict[str, HistoryDocument],
    *,
    turn_index: int,
    scene_id: str,
) -> dict[str, HistoryDocument]:
    """管线入口的唯一证据集合：本轮本场景的正文段落（mapping key == document id）。

    身份扫描、Prompt 投影与 admission 全部消费同一集合，避免错回合 / 错场景历史
    正文进入 resolved、影响字段判断或增加无关 token。
    """
    return {
        key: document
        for key, document in evidence_documents.items()
        if key == document.document_id
        and document.source_type == _SOURCE_SCENE_PARAGRAPH
        and document.turn_index == turn_index
        and document.scene_id == scene_id
    }


def apply_npc_batch_update(
    *,
    npc_store: NPCProfileStore,
    gateway: LLMGateway,
    scene_facts: SceneFactSet,
    evidence_documents: dict[str, HistoryDocument],
    current_state: CurrentState,
    turn_index: int,
    scene_id: str | None = None,
    admitted_at: str,
    trace_id: str | None = None,
) -> NPCBatchProcessResult:
    """完整管线：确定性解析 → 单次 batch 调用 → per-field 容错 admission → 应用。

    - 统一 ``current_scene_evidence``：身份扫描、Prompt 投影与 admission 同源；
    - snapshot 唯一 mutation 源：admission 的 ``updated_profiles``（字段 + 解析层
      待接受 alias 一并派生），store 只 ``upsert`` 它们；
    - mention hit：已解析 npc 的 observed surface 与已接受 alias surface 命中
      canonical / 永久 alias 才记（``surface_is_known``）；
    - 解析层的最小候选（未连接有名人物）交给确认 Director，不落库。
    """
    if scene_id is None:
        scene_id = current_state.scene.scene_id

    current_evidence = _current_scene_evidence(
        evidence_documents,
        turn_index=turn_index,
        scene_id=scene_id,
    )

    profiles = npc_store.list_all()
    resolution = resolve_npc_identities(
        profiles=profiles,
        npc_mentions=scene_facts.npc_mentions,
        paragraph_texts=[
            document.text for document in current_evidence.values()
        ],
        player_name=current_state.player.name,
    )

    parsed = NPCBatchUpdater(gateway).process(
        resolved=[
            item for item in resolution.resolved
            if item.presence == "appeared"
        ],
        profiles=profiles,
        scene_facts=scene_facts,
        evidence_documents=current_evidence,
        turn_index=turn_index,
        scene_id=scene_id,
        trace_id=trace_id,
    )

    alias_changes = [
        AcceptedNPCAliasChange(
            npc_id=change.npc_id,
            canonical_name=change.canonical_name,
            alias=change.surface,
            turn_index=turn_index,
            evidence_fact_ids=change.evidence_fact_ids,
            evidence_paragraph_ids=change.evidence_paragraph_ids,
        )
        for change in resolution.pending_alias_changes
    ]
    admission = admit_npc_batch_update(
        updates=parsed.entries,
        entry_warnings=parsed.entry_warnings,
        resolved_npc_ids=[item.npc_id for item in resolution.resolved],
        mutable_npc_ids=[
            item.npc_id for item in resolution.resolved
            if item.presence == "appeared"
        ],
        alias_changes=alias_changes,
        profiles=profiles,
        evidence_documents=current_evidence,
        current_state=current_state,
        turn_index=turn_index,
        scene_id=scene_id,
        admitted_at=admitted_at,
    )

    saved_ids: list[str] = []
    for profile in admission.updated_profiles:
        saved_ids.append(npc_store.upsert(profile))

    profile_view = {profile.npc_id: profile for profile in profiles}
    profile_view.update(
        {profile.npc_id: profile for profile in admission.updated_profiles}
    )
    pending_by_npc: dict[str, list[str]] = {}
    for change in resolution.pending_alias_changes:
        pending_by_npc.setdefault(change.npc_id, []).append(change.surface)
    for item in resolution.resolved:
        profile = profile_view.get(item.npc_id)
        if profile is None:
            continue
        seen_surfaces: set[str] = set()
        for surface in [
            *item.primary_surfaces,
            *(relation.surface for relation in item.alias_relations),
            *pending_by_npc.get(item.npc_id, []),
        ]:
            surface_key = normalize_compact_key(surface)
            if not surface_key or surface_key in seen_surfaces:
                continue
            seen_surfaces.add(surface_key)
            if surface_is_known(surface, profile):
                npc_store.record_mention(item.npc_id, surface)

    return NPCBatchProcessResult(
        saved_ids=saved_ids,
        new_npc_candidates=list(resolution.candidates),
        warnings=list(admission.warnings),
        accepted_changes=list(admission.accepted_profile_changes),
        accepted_alias_changes=list(admission.accepted_alias_changes),
        coverage=admission.coverage,
        resolution=resolution,
    )
