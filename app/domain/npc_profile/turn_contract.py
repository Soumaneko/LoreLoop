"""NPC batch field updater 的目标领域契约（Ticket 08，宁漏勿错）。

确定性身份解析（``identity_resolver``）已把「称呼→档案」归并完成，本契约只描述
单次 ``npc_batch_update`` 调用的输入/输出形状：

- 输入：每个已解析 NPC 的当前公开字段 + 本章相关 facts + 相关正文 evidence
  （由 ``build_npc_batch_input`` 投影）；
- 输出：宽松 wire ``NPCBatchUpdate.updates: list[Any]``，逐字段容错解析
  （``parse_batch_updates``）；prompt 展示用 typed ``NPCBatchUpdateDisplay``；
- 稳定字段变化为原子 change（``field`` 作 discriminator），范围限
  ``role / appearance / faction``；``last_scene_summary`` 走独立的
  ``scene_summary`` 槽位（每个 appeared NPC 每回合一条），``personality_trait``
  与 ``known_abilities`` 的逐回合更新暂时冻结，不进入 wire union；
- 每条稳定 change 必须携带本轮场景段落 ``evidence_ids``（结构校验，非语义）。

本文件不发起任何 LLM 调用。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.npc_profile.schemas import LLMKnownAbility

NPC_SCENE_SUMMARY_HARD_LIMIT = 160
NPC_SCENE_SUMMARY_SOFT_LIMIT = 240
CHANGE_BASIS_HARD_LIMIT = 80
CHANGE_BASIS_SOFT_LIMIT = 120


class _EvidenceCarrying(BaseModel):
    """携带 ``evidence_ids`` 的稳定 change 基类。

    只做结构校验：每个被写入的稳定字段必须引用本轮已确认 scene paragraph 的
    ``document_id``。去重在模型校验层执行（保序，不用无序 set）；JSON Schema 本身
    不承诺 ``uniqueItems``。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_ids: list[str] = Field(min_length=1, max_length=4)

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids_no_duplicates(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("evidence_ids must not repeat")
        return values


class ScalarStableChange(_EvidenceCarrying):
    """标量稳定字段的原子 set change。

    ``change_basis`` 是本次变化的可信自由文本（LLM 在同一调用内产出，与
    ``evidence_ids`` 共用同一轮正文依据）：只陈述本次真正新增/移除/改写了什么，
    不搬运 retained 旧事实。它持久化为 field-change node 的 node basis
    （``npc_field_change_logs.change_basis``），供 History bridge 触发与
    node-evidence support 使用；缺失（仅限旧日志）时该 node 降级为
    ``basis_complete=False``。
    """

    field: Literal["role", "appearance", "faction"]
    value: str = Field(min_length=1)
    change_basis: str = Field(min_length=1, max_length=CHANGE_BASIS_SOFT_LIMIT)


class ScalarStableChangeHard(_EvidenceCarrying):
    """prompt 展示 / 第一轮硬校验用的稳定 change（change_basis 硬上限 80）。"""

    field: Literal["role", "appearance", "faction"]
    value: str = Field(min_length=1)
    change_basis: str = Field(min_length=1, max_length=CHANGE_BASIS_HARD_LIMIT)


class AbilityAddChange(_EvidenceCarrying):
    """Reserved append-only ability contract; per-turn admission is frozen.

    保留该类供后续重新设计能力准入时复用，但故意不放入
    ``NPCStableChange``，因此当前 LLM Schema 无权输出它。
    """

    field: Literal["known_abilities"]
    ability: LLMKnownAbility


class NPCSceneSummaryDraft(BaseModel):
    """每个 appeared existing NPC 的本轮独立摘要草稿（``scene_summary`` 槽位）。

    只压缩本轮该 NPC 的公开言语、行动、决定与可观察状态；不接收旧摘要、不累积、
    不写跨回合传记。``summary`` 20–240 字（软上限；prompt 展示仍为 160 硬上限），
    ``evidence_ids`` 1–4 条本轮本场景正文段落（结构约束；语义归因由 prompt 保证）。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(
        min_length=20,
        max_length=NPC_SCENE_SUMMARY_SOFT_LIMIT,
    )
    evidence_ids: list[str] = Field(min_length=1, max_length=4)

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids_no_duplicates(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("scene_summary evidence_ids must not repeat")
        return values


class NPCSceneSummaryDraftHard(BaseModel):
    """prompt 展示 / 第一轮硬校验用的 scene_summary（summary 硬上限 160）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(
        min_length=20,
        max_length=NPC_SCENE_SUMMARY_HARD_LIMIT,
    )
    evidence_ids: list[str] = Field(min_length=1, max_length=4)

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids_no_duplicates(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("scene_summary evidence_ids must not repeat")
        return values


#: 稳定字段变化，以 ``field`` 作 discriminator（软上限 120）。
NPCStableChange = Annotated[
    ScalarStableChange,
    Field(discriminator="field"),
]

#: 稳定字段变化（硬上限 80），用于 prompt 展示与第一轮硬校验。
NPCStableChangeHard = Annotated[
    ScalarStableChangeHard,
    Field(discriminator="field"),
]


class NPCBatchUpdateEntry(BaseModel):
    """NPC batch field updater 的单个既有 NPC 条目（admission 输入 / display 形状）。

    ``npc_id`` 由确定性身份解析层给定；LLM 只输出字段变化，不再判断
    mention/alias/new/ignore，也不再生成新 NPC 完整档案。

    该模型是**已解析**的条目（``stable_changes`` 为 typed union；``scene_summary``
    为独立摘要槽位）；宽松 wire 解析由 ``parse_batch_updates`` 逐字段 salvage 后
    构造本模型。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    npc_id: str = Field(min_length=1)
    scene_summary: NPCSceneSummaryDraft | None = None
    stable_changes: list[NPCStableChange] = Field(default_factory=list, max_length=8)


class NPCBatchUpdate(BaseModel):
    """单次批量字段更新调用的 LLM 输出（宽松 wire envelope）。

    ``updates`` 为 ``list[Any]``：null / 非对象元素 / 单条目内部字段非法都不拖垮
    整批解析。逐字段 salvage 由下游 ``parse_batch_updates`` 完成（宁漏勿错）。
    顶层 extra 字段与 256 上限由 ``output_validator`` 处理，不在 schema 层连坐。
    该模型只用于 gateway 的宽容解析，不作为 prompt 展示 schema。
    """

    model_config = ConfigDict(extra="allow")

    updates: list[Any] = Field(default_factory=list)


class NPCBatchUpdateDisplayEntry(BaseModel):
    """prompt 展示用的单个 appeared NPC 条目。

    与解析/admission 用的 ``NPCBatchUpdateEntry`` 分离：``scene_summary`` 在这里
    **必填**——每个输入 NPC 都必须输出本轮摘要，即使 stable 字段无变化。宽松 wire
    解析仍以 optional 构造 ``NPCBatchUpdateEntry`` 以便 salvage（漏写 → admission
    记 ``missing_scene_summary``）。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    npc_id: str = Field(min_length=1)
    scene_summary: NPCSceneSummaryDraftHard
    stable_changes: list[NPCStableChangeHard] = Field(default_factory=list, max_length=8)


class NPCBatchUpdateDisplay(BaseModel):
    """prompt 展示用的完整形状 schema（结构约束由此表达）。

    与宽松 wire ``NPCBatchUpdate`` 分离：模型看到 typed 条目与全部 field variant /
    evidence_ids / ability 结构，程序解析仍逐字段容错。
    """

    model_config = ConfigDict(extra="forbid")

    updates: list[NPCBatchUpdateDisplayEntry] = Field(
        default_factory=list, max_length=256
    )
