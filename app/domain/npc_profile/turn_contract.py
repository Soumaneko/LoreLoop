"""NPC batch field updater 的目标领域契约（Ticket 08，宁漏勿错）。

确定性身份解析（``identity_resolver``）已把「称呼→档案」归并完成，本契约只描述
单次 ``npc_batch_update`` 调用的输入/输出形状：

- 输入：每个已解析 NPC 的当前公开字段 + 本章相关 facts + 相关正文 evidence
  （由 ``build_npc_batch_input`` 投影）；
- 输出：宽松 wire ``NPCBatchUpdate.updates: list[Any]``，逐字段容错解析
  （``parse_batch_updates``）；prompt 展示用 typed ``NPCBatchUpdateDisplay``；
- 稳定字段变化为原子 change（``field`` 作 discriminator），``public_summary``
  强制 ``summary_delta``；``personality_trait`` 与 ``known_abilities`` 的逐回合更新
  暂时冻结，不进入 wire union；
- 每条稳定 change 必须携带本轮场景段落 ``evidence_ids``（结构校验，非语义）。

本文件不发起任何 LLM 调用。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.npc_profile.schemas import LLMKnownAbility


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
    """标量稳定字段的原子 set change。"""

    field: Literal["role", "appearance", "faction"]
    value: str = Field(min_length=1)


class PublicSummaryChange(_EvidenceCarrying):
    """``public_summary`` 的原子 set change，必须携带 ``summary_delta``。

    ``summary_delta`` 只描述本次重大变化，未来 History bridge 以 delta 而非完整
    summary 作为 relation basis。
    """

    field: Literal["public_summary"]
    value: str = Field(min_length=1)
    summary_delta: str = Field(min_length=1)


class AbilityAddChange(_EvidenceCarrying):
    """Reserved append-only ability contract; per-turn admission is frozen.

    保留该类供后续重新设计能力准入时复用，但故意不放入
    ``NPCStableChange``，因此当前 LLM Schema 无权输出它。
    """

    field: Literal["known_abilities"]
    ability: LLMKnownAbility


#: 稳定字段变化，以 ``field`` 作 discriminator。
NPCStableChange = Annotated[
    Union[
        ScalarStableChange,
        PublicSummaryChange,
    ],
    Field(discriminator="field"),
]


class NPCBatchUpdateEntry(BaseModel):
    """NPC batch field updater 的单个既有 NPC 条目（admission 输入 / display 形状）。

    ``npc_id`` 由确定性身份解析层给定；LLM 只输出字段变化，不再判断
    mention/alias/new/ignore，也不再生成新 NPC 完整档案。``current_intent``
    空白会被归一化为 None（即不修改）。

    该模型是**已解析**的条目（``stable_changes`` 为 typed union）；宽松 wire 解析
    由 ``parse_batch_updates`` 逐字段 salvage 后构造本模型。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    npc_id: str = Field(min_length=1)
    current_intent: str | None = None
    stable_changes: list[NPCStableChange] = Field(default_factory=list, max_length=8)

    @field_validator("current_intent")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class NPCBatchUpdate(BaseModel):
    """单次批量字段更新调用的 LLM 输出（宽松 wire envelope）。

    ``updates`` 为 ``list[Any]``：null / 非对象元素 / 单条目内部字段非法都不拖垮
    整批解析。逐字段 salvage 由下游 ``parse_batch_updates`` 完成（宁漏勿错）。
    该模型只用于 gateway 的宽容解析，不作为 prompt 展示 schema。
    """

    model_config = ConfigDict(extra="forbid")

    updates: list[Any] = Field(default_factory=list, max_length=256)


class NPCBatchUpdateDisplay(BaseModel):
    """prompt 展示用的完整形状 schema（结构约束由此表达）。

    与宽松 wire ``NPCBatchUpdate`` 分离：模型看到 typed 条目与全部 field variant /
    evidence_ids / summary_delta / ability 结构，程序解析仍逐字段容错。
    """

    model_config = ConfigDict(extra="forbid")

    updates: list[NPCBatchUpdateEntry] = Field(default_factory=list, max_length=256)
