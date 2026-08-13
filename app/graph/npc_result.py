from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.npc_profile.batch_updater import NPCBatchProcessResult
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_facts.schemas import NpcPresence


@dataclass(frozen=True)
class NpcCandidateContext:
    """图侧候选上下文：薄壳 NPCProfile 之外的 alias、证据与出场类别（供 Director 展示）。"""

    primary_surface: str
    presence: NpcPresence
    alias_surfaces: list[str] = field(default_factory=list)
    evidence_fact_ids: list[str] = field(default_factory=list)
    evidence_paragraph_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NpcTurnNodeResult:
    """NPC 节点单 slot 容器：process_result 语义不被 prune/候选派生污染。"""

    process_result: NPCBatchProcessResult
    updated_npc_profile_ids: list[str]
    new_npc_profiles: list[NPCProfile]
    candidate_context: dict[str, NpcCandidateContext]
