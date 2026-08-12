"""NPC batch change admission 的结果契约（Ticket 08）。

本文件只定义可序列化的 accepted mutation 契约与结果容器，不包含任何判断逻辑
（逻辑在 ``change_admission.py``）。

``AcceptedNPCFieldChange`` 按 ``operation`` 做 discriminated union，从 schema 层
禁止自相矛盾的数据：

- ``set``：标量字段原子更新；``summary_delta`` 仅 ``public_summary`` 且必填；
  ``current_state_sync`` 来源不得携带正文 evidence；proposal 来源的稳定字段必须
  携带 evidence、``current_intent`` 不得携带；
- ``add``：``known_abilities`` append-only 追加，``additions`` 非空且去重。

alias 以显式 ``AcceptedNPCAliasChange`` 表达（携带 fact + paragraph 证据），与字段
变化同属 accepted mutation 单一来源；snapshot application 只消费这两类变化与
``new_npc_candidates``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.text import normalize_compact_key
from app.domain.npc_profile.schemas import KnownAbility, NPCProfile

#: proposal 来源、必须携带 evidence 的稳定字段。
_STABLE_PROPOSAL_FIELDS = frozenset(
    {"public_summary", "role", "appearance", "faction"}
)

#: CurrentState 独占同步字段（proposal 不得写入）。
SYNC_FIELDS = frozenset({"last_known_location", "attitude_to_player"})

SCALAR_FIELD_LITERAL = Literal[
    "public_summary",
    "role",
    "appearance",
    "faction",
    "current_intent",
    "last_known_location",
    "attitude_to_player",
]


class AcceptedNPCScalarChange(BaseModel):
    """标量字段的原子 set change（operation="set"）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation: Literal["set"] = "set"
    npc_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    field: SCALAR_FIELD_LITERAL
    old_value: str | None = None
    new_value: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    turn_index: int | None = None
    summary_delta: str | None = None
    source: Literal["proposal", "current_state_sync"] = "proposal"

    @model_validator(mode="after")
    def _summary_delta_only_for_public_summary(self):
        if self.field == "public_summary":
            if not self.summary_delta:
                raise ValueError("summary_delta is required for public_summary changes")
        elif self.summary_delta is not None:
            raise ValueError("summary_delta is only valid for public_summary changes")
        return self

    @model_validator(mode="after")
    def _sync_changes_carry_no_evidence(self):
        if self.source == "current_state_sync":
            if self.field not in SYNC_FIELDS:
                raise ValueError(
                    "current_state_sync changes only apply to "
                    "last_known_location / attitude_to_player"
                )
            if self.evidence_ids:
                raise ValueError("current_state_sync changes must not carry evidence")
        return self

    @model_validator(mode="after")
    def _proposal_field_evidence_contract(self):
        if self.source != "proposal":
            return self
        if self.field in SYNC_FIELDS:
            raise ValueError(
                "last_known_location / attitude_to_player are "
                "current_state_sync only"
            )
        if self.field in _STABLE_PROPOSAL_FIELDS:
            if not self.evidence_ids:
                raise ValueError("stable field changes require evidence_ids")
        elif self.evidence_ids:
            raise ValueError("current_intent changes must not carry evidence_ids")
        return self


class AcceptedNPCAbilityAddChange(BaseModel):
    """``known_abilities`` 的 append-only add change（operation="add"）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation: Literal["add"] = "add"
    npc_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    field: Literal["known_abilities"] = "known_abilities"
    additions: list[KnownAbility] = Field(min_length=1, max_length=5)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    turn_index: int | None = None
    source: Literal["proposal"] = "proposal"

    @field_validator("additions")
    @classmethod
    def _abilities_no_duplicates(cls, values):
        seen: set[str] = set()
        for ability in values:
            key = normalize_compact_key(ability.name)
            if not key or key in seen:
                raise ValueError("additions must not contain duplicate abilities")
            seen.add(key)
        return values


#: 字段/能力两类 accepted mutation，以 ``operation`` 作 discriminator。
AcceptedNPCFieldChange = Annotated[
    Union[AcceptedNPCScalarChange, AcceptedNPCAbilityAddChange],
    Field(discriminator="operation"),
]


@dataclass(frozen=True)
class AcceptedNPCAliasChange:
    npc_id: str
    canonical_name: str
    alias: str
    turn_index: int | None = None
    #: 支持该 alias 关系的 Scene Fact 证据（fact 引用与正文段落引用可独立并存，
    #: 供未来 alias/field log bridge）。
    evidence_fact_ids: tuple[str, ...] = ()
    evidence_paragraph_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NPCChangeAdmissionWarning:
    npc_name: str
    field_name: str
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NPCBatchCoverage:
    """NPC batch field updater 的覆盖诊断（宁漏勿错语义）。"""

    resolved_npcs: int
    updated_npcs: int
    no_op_npcs: int
    dropped_entries: int
    synced_npcs: int


@dataclass
class NPCChangeAdmissionResult:
    accepted_profile_changes: list[AcceptedNPCFieldChange] = field(default_factory=list)
    accepted_alias_changes: list[AcceptedNPCAliasChange] = field(default_factory=list)
    new_npc_candidates: list[NPCProfile] = field(default_factory=list)
    warnings: list[NPCChangeAdmissionWarning] = field(default_factory=list)
    coverage: NPCBatchCoverage | None = None
    updated_profiles: list[NPCProfile] = field(default_factory=list)


class NPCTurnAdmissionError(ValueError):
    """整批被拒绝的基类：由模型输出或确定性输入触发的 admission 拒绝。

    离线 benchmark 与录制路径把此类拒绝记为 invariant 并继续；真正的程序错误
    （scene_id 不匹配、重复 npc_id、投影不一致）仍抛普通 ``ValueError`` 传播。
    """
