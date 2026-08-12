"""NPC Turn Processor 的目标领域契约（Ticket 02）。

定义确定性的 ``NPCTurnRequest`` 与严格 discriminated union 的
``NPCTurnProposal``，并在 schema 层与硬校验层禁止非法字段组合：

- 每个 proposal variant 只携带自身允许的字段（``resolution`` 作 discriminator），
  JSON Schema 以 ``oneOf`` 表达，非法组合在解析时即被拒绝；
- exact 身份以稳定 ``npc_id`` 寻址，不再依赖 Prompt 临时数组 index；
- ``temporary_patch`` 改为 ``current_patch``，只保留仍由 NPC processor 决定的
  ``current_intent``；location/attitude 由 CurrentState 同步（本文件只冻结字段集合，
  应用与互斥在 03 admission 落地）；
- ``long_term_patch + field_evidence`` 替换为按字段区分的原子 change
  （``field`` 作 discriminator），``public_summary`` 强制携带 ``summary_delta``，
  ``known_abilities`` 为 append-only add change；
- 删除无消费者的 ``turn_observation``；
- 玩家以 ``protected_player_identity`` 表达，不再作为伪 NPC option。

本文件不发起任何 LLM 调用；单次 processor 的请求投影、prompt 序列化由 Ticket 04
负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.text import normalize_compact_key
from app.domain.npc_profile.schemas import LLMKnownAbility, NPCNewProfileOutput
from app.domain.scene_facts.schemas import SceneFactSet

#: 仍由 NPC processor（LLM）决定的最近已知字段；其余临时字段由 CurrentState 同步。
NPC_TURN_LLM_CURRENT_FIELDS = frozenset({"current_intent"})

#: 由 updated CurrentState 确定性同步、NPC processor 不得写入的字段。
CURRENT_STATE_SYNCED_FIELDS = frozenset({"last_known_location", "attitude_to_player"})

#: 全 batch 内 ``(target_npc_id, scalar_field)`` 必须唯一的标量稳定字段。
SCALAR_STABLE_FIELDS = frozenset(
    {"role", "appearance", "personality_trait", "faction", "public_summary"}
)

#: 防御性上限，保持不变量链：
#: ``len(exact_targets) <= len(identity_options) <= MAX_IDENTITY_OPTIONS`` 且
#: ``MAX_EXACT_TARGETS <= MAX_PROPOSALS``——因此任意合法 request 的 exact 覆盖校验
#: 恒可用 existing proposals 满足（每个 exact 至少一条 proposal）。
MAX_IDENTITY_OPTIONS = 200
MAX_EXACT_TARGETS = 200
MAX_PROPOSALS = 256


class NPCIdentityOption(BaseModel):
    """候选池中的单个身份选项：稳定 npc_id + 用于 alias 判定的公开字段。

    ``aliases`` 已在构造时合并永久 alias 与未过期的 transient alias，并做规范化
    去重；模型以此进行 alias_match，不再依赖数组 index。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    npc_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    role: str | None = None
    public_summary: str | None = None
    appearance: str | None = None
    faction: str | None = None
    last_known_location: str | None = None


class SceneParagraphRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class NPCTurnRequest(BaseModel):
    """NPCTurnProcessor 的领域请求数据（不含 LLM 传输细节）。

    ``output_schema_json`` 属于 Ticket 04 的 prompt 序列化，不进入本模型。
    """

    model_config = ConfigDict(extra="forbid")

    #: unchanged Scene Facts 负载（有效 ``SceneFactSet`` JSON）。
    scene_facts: str = Field(min_length=1)
    #: updated CurrentState 的精简投影（由 Ticket 04 的投影器生成）。
    current_state_summary: str = ""
    #: 程序从全部档案精确命中的 NPC；identity_options 必须包含全部 exact targets。
    exact_targets: list[NPCIdentityOption] = Field(
        default_factory=list, max_length=MAX_EXACT_TARGETS
    )
    #: 供 unresolved 表面称呼选择的有界候选池。
    identity_options: list[NPCIdentityOption] = Field(
        default_factory=list, max_length=MAX_IDENTITY_OPTIONS
    )
    #: 明确告知模型不得创建/匹配为 NPC 的玩家身份。
    protected_player_identity: str = Field(min_length=1)
    #: 本轮已确认 scene paragraph evidence（document_id + text）。
    scene_paragraph_evidence: list[SceneParagraphRef] = Field(
        default_factory=list, max_length=200
    )
    #: 仅当 new NPC 可能存在时提供的必要世界公开基线；默认不提供 recent summaries。
    world_baseline: str | None = None

    @model_validator(mode="after")
    def _scene_facts_must_be_valid_scene_fact_set(self):
        SceneFactSet.model_validate_json(self.scene_facts)
        return self

    @model_validator(mode="after")
    def _exact_targets_unique(self):
        ids = [option.npc_id for option in self.exact_targets]
        if len(ids) != len(set(ids)):
            raise ValueError("exact_targets npc_id values must be unique")
        return self

    @model_validator(mode="after")
    def _identity_options_unique(self):
        ids = [option.npc_id for option in self.identity_options]
        if len(ids) != len(set(ids)):
            raise ValueError("identity_options npc_id values must be unique")
        return self

    @model_validator(mode="after")
    def _exact_targets_are_identity_options(self):
        by_id = {option.npc_id: option for option in self.identity_options}
        for option in self.exact_targets:
            identity = by_id.get(option.npc_id)
            if identity is None:
                raise ValueError(
                    f"exact target {option.npc_id!r} is missing from identity_options"
                )
            if option.model_dump() != identity.model_dump():
                raise ValueError(
                    f"exact target {option.npc_id!r} projection differs from "
                    "its identity_options entry"
                )
        return self

    @model_validator(mode="after")
    def _identity_options_must_not_contain_player(self):
        player = normalize_compact_key(self.protected_player_identity)
        for option in self.identity_options:
            if any(
                normalize_compact_key(value) == player
                for value in [option.name, *option.aliases]
            ):
                raise ValueError(
                    f"identity option {option.npc_id!r} carries the protected "
                    "player identity"
                )
        return self


class NPCTurnCurrentPatch(BaseModel):
    """最近已知字段：只包含仍由 NPC processor 决定的字段（当前为 current_intent）。

    ``None`` = 不修改；空白字符串会被归一化为 None，因此本通道无法清空字段。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    current_intent: str | None = None

    @field_validator("current_intent")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


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

    field: Literal["role", "appearance", "personality_trait", "faction"]
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
    """``known_abilities`` 的 append-only add change（移除能力不开放）。"""

    field: Literal["known_abilities"]
    ability: LLMKnownAbility


#: 稳定字段变化，以 ``field`` 作 discriminator。
NPCStableChange = Annotated[
    Union[ScalarStableChange, PublicSummaryChange, AbilityAddChange],
    Field(discriminator="field"),
]


class ExistingNPCProposal(BaseModel):
    """exact 命中既有档案：必须携带稳定 ``target_npc_id``。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolution: Literal["existing"]
    surface: str = Field(min_length=1)
    context_spans: list[str] = Field(min_length=1, max_length=8)
    target_npc_id: str = Field(min_length=1)
    current_patch: NPCTurnCurrentPatch | None = None
    stable_changes: list[NPCStableChange] = Field(default_factory=list, max_length=8)


class AliasMatchProposal(BaseModel):
    """unresolved 表面称呼命中候选池中的既有档案。

    默认不持久化 alias（policy B：称号类描述可 resolve 但不得沉淀为永久 alias，
    防易主误归因）；只有明确固定昵称才设 ``persist_alias=True``。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolution: Literal["alias_match"]
    surface: str = Field(min_length=1)
    context_spans: list[str] = Field(min_length=1, max_length=8)
    target_npc_id: str = Field(min_length=1)
    persist_alias: bool = False
    current_patch: NPCTurnCurrentPatch | None = None
    stable_changes: list[NPCStableChange] = Field(default_factory=list, max_length=8)


class NewNPCProposal(BaseModel):
    """未识别且需要持续功能准入的新 NPC 候选。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolution: Literal["new"]
    surface: str = Field(min_length=1)
    context_spans: list[str] = Field(min_length=1, max_length=8)
    new_profile: NPCNewProfileOutput


class IgnoreMentionProposal(BaseModel):
    """一次性/组织/地点等无需建档的表面称呼。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolution: Literal["ignore"]
    surface: str = Field(min_length=1)
    context_spans: list[str] = Field(min_length=1, max_length=8)


#: 单个 mention 的处理 proposal，以 ``resolution`` 作 discriminator。
#: schema 层禁止 resolution/patch/new_profile 的非法组合：existing/alias 必须有
#: ``target_npc_id`` 且不得带 ``new_profile``；new 必须有 ``new_profile``；
#: ignore 两者都不得携带。
NPCTurnProposal = Annotated[
    Union[
        ExistingNPCProposal,
        AliasMatchProposal,
        NewNPCProposal,
        IgnoreMentionProposal,
    ],
    Field(discriminator="resolution"),
]


class NPCTurnProposalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[NPCTurnProposal] = Field(default_factory=list, max_length=MAX_PROPOSALS)


@dataclass(frozen=True)
class NPCProposalValidationError:
    code: str
    message: str
    proposal_index: int | None = None
    npc_id: str | None = None
    surface: str | None = None
    field_name: str | None = None


def validate_npc_turn_proposals(
    request: NPCTurnRequest,
    proposals: list[NPCTurnProposal],
) -> list[NPCProposalValidationError]:
    """对整批 proposal 做硬校验，返回（保序）错误列表。

    错误按 ``(proposal_index, code)`` 排序，跨进程稳定。规则：

    - unknown ``target_npc_id``：必须出现在 request 的 identity_options；
    - ``existing`` 必须指向 exact_targets 内的 npc_id，且其 ``surface`` 必须命中
      目标 option 的 name/aliases（aliases 已含未过期 transient alias）；
    - 重复 surface：同一归一化表面称呼只能有一条 proposal；
    - ``(target_npc_id, scalar_field)`` 全 batch 唯一：同一 NPC 的多条 proposal
      不得同时写同一标量稳定字段；
    - ``(target_npc_id, current_field)`` 全 batch 唯一且值感知：同一 NPC 的多条
      proposal 写 ``current_intent`` 时，相同值去重、冲突值报错
      （``duplicate_current_field``）；
    - ``known_abilities`` 允许多条 add，但 ``(target_npc_id, normalize_compact_key(name))``
      不得重复；
    - player 保护：surface / new_profile.name / new_profile.aliases 不得等于
      protected_player_identity（target 不可能是玩家——request 不变量已排除玩家
      身份进 option）；
    - ``new_profile.name`` 归一化必须等于 surface；
    - exact target 覆盖：每个 exact npc_id 至少被一条 ``existing`` proposal 指向。
    """
    identity_ids = {option.npc_id for option in request.identity_options}
    identity_by_id = {option.npc_id: option for option in request.identity_options}
    exact_ids = {option.npc_id for option in request.exact_targets}
    player = normalize_compact_key(request.protected_player_identity)

    errors: list[NPCProposalValidationError] = []
    for index, proposal in enumerate(proposals):
        surface_key = normalize_compact_key(proposal.surface)
        if surface_key == player:
            errors.append(NPCProposalValidationError(
                code="player_surface",
                message=(
                    "proposal surface must not reference the protected player "
                    "identity"
                ),
                proposal_index=index,
                surface=proposal.surface,
            ))
        if isinstance(proposal, (ExistingNPCProposal, AliasMatchProposal)):
            target_known = proposal.target_npc_id in identity_ids
            if not target_known:
                errors.append(NPCProposalValidationError(
                    code="unknown_target_npc_id",
                    message=(
                        "target_npc_id must reference an identity_options entry"
                    ),
                    proposal_index=index,
                    npc_id=proposal.target_npc_id,
                    surface=proposal.surface,
                ))
            if (
                isinstance(proposal, ExistingNPCProposal)
                and proposal.target_npc_id not in exact_ids
            ):
                errors.append(NPCProposalValidationError(
                    code="existing_without_exact_target",
                    message=(
                        "existing resolution requires an exact-target npc_id; "
                        "use alias_match or fix the candidate scope"
                    ),
                    proposal_index=index,
                    npc_id=proposal.target_npc_id,
                    surface=proposal.surface,
                ))
            if (
                isinstance(proposal, ExistingNPCProposal)
                and target_known
                and surface_key != player
                and not _surface_matches_option(
                    surface_key, identity_by_id[proposal.target_npc_id]
                )
            ):
                errors.append(NPCProposalValidationError(
                    code="existing_surface_mismatch",
                    message=(
                        "existing surface must match the target option's "
                        "name or aliases"
                    ),
                    proposal_index=index,
                    npc_id=proposal.target_npc_id,
                    surface=proposal.surface,
                ))
        elif isinstance(proposal, NewNPCProposal):
            if any(
                normalize_compact_key(value) == player
                for value in [
                    proposal.new_profile.name,
                    *proposal.new_profile.aliases,
                ]
            ):
                errors.append(NPCProposalValidationError(
                    code="player_new_profile",
                    message=(
                        "new_profile name and aliases must not reference the "
                        "protected player identity"
                    ),
                    proposal_index=index,
                    surface=proposal.surface,
                ))
            if (
                normalize_compact_key(proposal.new_profile.name)
                != normalize_compact_key(proposal.surface)
            ):
                errors.append(NPCProposalValidationError(
                    code="new_profile_name_mismatch",
                    message=(
                        "new_profile.name must match the mention surface"
                    ),
                    proposal_index=index,
                    surface=proposal.surface,
                ))

    seen_surface: dict[str, int] = {}
    surface_reported: set[str] = set()
    for index, proposal in enumerate(proposals):
        surface_key = normalize_compact_key(proposal.surface)
        first = seen_surface.get(surface_key)
        if first is None:
            seen_surface[surface_key] = index
            continue
        if surface_key in surface_reported:
            continue
        surface_reported.add(surface_key)
        errors.append(NPCProposalValidationError(
            code="duplicate_surface",
            message=(
                "the same mention surface must not have multiple proposals"
            ),
            proposal_index=first,
            surface=proposal.surface,
        ))

    scalar_first: dict[tuple[str, str], int] = {}
    scalar_reported: set[tuple[str, str]] = set()
    ability_first: dict[tuple[str, str], int] = {}
    ability_reported: set[tuple[str, str]] = set()

    current_field_first: dict[tuple[str, str], tuple[int, str]] = {}
    current_field_reported: set[tuple[str, str]] = set()
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, (ExistingNPCProposal, AliasMatchProposal)):
            continue
        patch = proposal.current_patch
        if patch is None or patch.current_intent is None:
            continue
        key = (proposal.target_npc_id, "current_intent")
        value = patch.current_intent
        first = current_field_first.get(key)
        if first is None:
            current_field_first[key] = (index, value)
            continue
        if key in current_field_reported:
            continue
        if value == first[1]:
            continue
        current_field_reported.add(key)
        errors.append(NPCProposalValidationError(
            code="duplicate_current_field",
            message=(
                "current field 'current_intent' is proposed with conflicting "
                "values for the same target npc"
            ),
            proposal_index=first[0],
            npc_id=proposal.target_npc_id,
            field_name="current_intent",
        ))

    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, (ExistingNPCProposal, AliasMatchProposal)):
            continue
        for change in proposal.stable_changes:
            if isinstance(change, (ScalarStableChange, PublicSummaryChange)):
                key = (proposal.target_npc_id, change.field)
                first = scalar_first.get(key)
                if first is None:
                    scalar_first[key] = index
                    continue
                if key in scalar_reported:
                    continue
                scalar_reported.add(key)
                errors.append(NPCProposalValidationError(
                    code="duplicate_stable_field",
                    message=(
                        f"stable field {change.field!r} is proposed more than "
                        "once for the same target npc"
                    ),
                    proposal_index=first,
                    npc_id=proposal.target_npc_id,
                    field_name=change.field,
                ))
            elif isinstance(change, AbilityAddChange):
                key = (proposal.target_npc_id, normalize_compact_key(change.ability.name))
                first = ability_first.get(key)
                if first is None:
                    ability_first[key] = index
                    continue
                if key in ability_reported:
                    continue
                ability_reported.add(key)
                errors.append(NPCProposalValidationError(
                    code="duplicate_ability_add",
                    message=(
                        "the same known ability is added more than once for "
                        "the same target npc"
                    ),
                    proposal_index=first,
                    npc_id=proposal.target_npc_id,
                    field_name=change.ability.name,
                ))

    proposed_exact = {
        proposal.target_npc_id
        for proposal in proposals
        if isinstance(proposal, ExistingNPCProposal)
        and proposal.target_npc_id in identity_ids
    }
    for npc_id in sorted(exact_ids):
        if npc_id not in proposed_exact:
            errors.append(NPCProposalValidationError(
                code="exact_target_missing",
                message="exact target must be resolved by an existing proposal",
                npc_id=npc_id,
            ))

    return sorted(
        errors,
        key=lambda error: (
            error.proposal_index if error.proposal_index is not None else -1,
            error.code,
        ),
    )


def _surface_matches_option(surface_key: str, option: NPCIdentityOption) -> bool:
    """``existing`` 的 surface 必须命中目标 option 的 name 或任一 alias。"""
    return any(
        normalize_compact_key(value) == surface_key
        for value in [option.name, *option.aliases]
    )
