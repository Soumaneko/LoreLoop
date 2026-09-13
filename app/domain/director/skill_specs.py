"""Director Agent 技能语义规则与参数契约（单一来源）。

本文件集中声明导演 Agent 可用的全部技能及其选择语义，供调试与评审时
直接对照，而无需深入 LangGraph 编排代码。每个条目包含：

- ``name``：技能标识，与注册和执行时的 skill 名称一致。
- ``description``：提交给模型的语义规则（模型据此决定是否选择该技能）。
- ``phases``：允许出现的阶段（generation / confirmation）。
- ``mutates``：是否在确认阶段变更持久状态（生成阶段禁止变更）。
- ``arguments_model``：参数 JSON Schema 的来源。

运行时请通过 :func:`get_director_skill_specs` 获取条目，避免本模块在
导入期被 LangGraph 缓存副作用所影响。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.domain.director.schemas import DirectorPhase
from app.domain.npc_profile.schemas import (
    NPC_PERSONALITY_TRAIT_MAX_LENGTH,
    NPCDisclosureLevel,
)
from app.domain.world_setting.schemas import SettingKind


# ---------------------------------------------------------------------------
# 参数模型
# ---------------------------------------------------------------------------


#: 披露等级维度（复用 view_mode 三级语义）：remote < face_to_face < intimate。
NPCViewMode = NPCDisclosureLevel

#: 无论何种披露等级都必须保留的身份/来源键。
NPC_VIEW_ALWAYS_KEPT = frozenset({"source", "npc_id", "draft_id", "name"})


class PresentEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    hook_id: str = Field(
        min_length=1,
        max_length=80,
        description=(
            "本技能只服务潜伏期(hidden)事件且只能通过 hook 间接泄露："
            "必须指定要呈现给 Composer 的 hook_id；不指定即视为非法请求。"
        ),
    )
    actors: list[str] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "可选：本轮随该演出安排出场的 NPC 姓名。用于把 hidden 事件相关的"
            "已有 NPC 纳入本轮'安排参与人物'，供 Composer 补齐档案、确认端识别身份；"
            "省略则本次演出不额外安排人物。"
        ),
    )


class IntrudeEngagedEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    actors: list[str] = Field(
        max_length=6,
        description=(
            "必填：本轮让事件中哪些具名演员出场；空数组 [] 明确表示不安排 NPC。"
            "只列出的演员进入本轮'安排参与人物'，供 Composer 补齐档案、确认端识别身份；"
            "未列出的演员不会自动入场。空白姓名或不在事件演员表内的姓名会拒绝整个请求。"
        ),
    )


class KnownAbilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=25, description="能力或特长名称（<=25字，建议<=10字）")
    description: str = Field(min_length=1, max_length=180, description="能力简要说明（<=180字，建议<=60字）")


class SpontaneousNPCSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        min_length=1,
        max_length=40,
        description="角色姓名（<=40字，不得与既有登场 NPC、别名、主角姓名或现有 drafts 重名）",
    )
    role: str = Field(
        min_length=1,
        max_length=100,
        description="身份或职业定位（<=100字，建议<=20字）",
    )
    appearance: str = Field(
        min_length=1,
        max_length=300,
        description="外貌、体型、服饰与特征描写（<=300字，建议<=100字）",
    )
    personality_trait: str = Field(
        min_length=1,
        max_length=NPC_PERSONALITY_TRAIT_MAX_LENGTH,
        description=f"核心性格特质标签（<={NPC_PERSONALITY_TRAIT_MAX_LENGTH}字，建议10-20字）",
    )
    known_abilities: list[KnownAbilitySpec] = Field(
        default_factory=list,
        max_length=5,
        description="简要已知能力或专业技能（最多5项）",
    )


class IntroduceNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    npc_id: str | None = Field(
        default=None,
        description="从 resources.drafts 中选取的准备好的 NPC 草稿 ID",
    )
    spontaneous: SpontaneousNPCSpec | None = Field(
        default=None,
        description="仅当 resources.drafts 中无匹配当前突发情境的存货时，填写的即兴人物卡",
    )
    view_mode: NPCViewMode = Field(
        description=(
            "选择该 NPC 首次披露的等级。起用已准备草稿时可选 remote/face_to_face/intimate；"
            "即兴新建 NPC（spontaneous）必须且只能使用 face_to_face（初次当面接触）。"
        ),
    )

    @model_validator(mode="after")
    def _validate_mutually_exclusive(self) -> IntroduceNPCArguments:
        if self.npc_id is not None and not self.npc_id.strip():
            self.npc_id = None
        has_id = self.npc_id is not None
        has_spontaneous = self.spontaneous is not None
        if has_id == has_spontaneous:
            raise ValueError(
                "introduce_npc requires exactly one of 'npc_id' (from resources.drafts) "
                "or 'spontaneous' (for JIT creation)"
            )
        if has_spontaneous and self.view_mode != "face_to_face":
            raise ValueError(
                "spontaneous NPC introduction requires view_mode 'face_to_face' "
                "(remote and intimate are not permitted for spontaneous creation)"
            )
        return self


class UpgradeDisclosureArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    new_disclosure_level: NPCViewMode = Field(
        description=(
            "将 NPC 的披露等级提升到该等级（只能提升，不能回退）："
            "remote < face_to_face < intimate。提升后当回合即可使用升级后的信息，"
            "并会在确认阶段持久化到档案。"
        )
    )


class SettingRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: SettingKind
    setting_description: str = Field(
        min_length=1,
        max_length=500,
        description="描述 setting 本身，而不是 npc 和 事件"
    )
    matched_setting_id: str | None = None


class CreateNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_requirements: list[SettingRequirement] = Field(
        min_length=1,
        max_length=1,
    )


class ExistingEventActorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: Literal["existing"]
    npc_id: str = Field(min_length=1)
    guidance: str = Field(default="", max_length=500)


class NewEventActorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: Literal["new"]
    guidance: str = Field(min_length=1, max_length=500)


EventActorInput = Annotated[
    ExistingEventActorInput | NewEventActorInput,
    Field(discriminator="source"),
]


class CreateEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    setting_requirements: list[SettingRequirement] = Field(
        min_length=1,
        max_length=1,
    )
    actors: list[EventActorInput] = Field(min_length=1, max_length=6)

    @model_validator(mode="before")
    @classmethod
    def _require_actors(cls, data):
        if isinstance(data, dict) and not data.get("actors"):
            raise ValueError(
                "create_event requires at least one real actor: use "
                "source='existing' with a real npc_id, or source='new' with a "
                "creation request; an organization name cannot replace an actor "
                "and actor-less events are not allowed"
            )
        return data

    @model_validator(mode="after")
    def _validate_actor_mix(self):
        new_count = sum(actor.source == "new" for actor in self.actors)
        if new_count > 3:
            raise ValueError("create_event supports at most 3 new actors")
        existing_ids = [
            actor.npc_id
            for actor in self.actors
            if actor.source == "existing"
        ]
        if len(existing_ids) != len(set(existing_ids)):
            raise ValueError("create_event has duplicate existing npc_id")
        return self


class BindEventActorArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    npc_id: str = Field(min_length=1)



# ---------------------------------------------------------------------------
# 技能语义规则
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectorSkillSpec:
    """一个导演技能的选择语义与参数契约。"""

    name: str
    description: str
    phases: frozenset[DirectorPhase]
    mutates: bool
    arguments_model: type[BaseModel]
    input_targets: dict[str | tuple[str, ...], int] = field(default_factory=dict)
    max_requests_per_batch: int | None = None


_GENERATION: frozenset[DirectorPhase] = frozenset({"generation"})
_CONFIRMATION: frozenset[DirectorPhase] = frozenset({"confirmation"})


_DIRECTOR_SKILL_SPECS: tuple[DirectorSkillSpec, ...] = (
    DirectorSkillSpec(
        name="present_event",
        description=(
            "将一个 hidden 事件通过间接接触泄露进本轮上下文。适用场景："
            "主角通过传闻、异象、信件、远程通讯或现场遗留痕迹，间接接触到了与该事件相关的信息。"
            "必须用 arguments.hook_id 选择本轮要呈现的 Hook；"
            "不指定 hook_id 时不呈现任何内容。必须在请求级 guidance 中说明具体表现方式。"
            "可用可选的 arguments.actors 声明本轮随该演出安排出场的已有 NPC 姓名。"
            "注意：engaged 事件无需也不允许用本技能"
            "重复引入；本技能仅限 hidden 且本轮未被玩家直接行动触及的事件。"
        ),
        phases=_GENERATION,
        mutates=False,
        arguments_model=PresentEventArguments,
    ),
    DirectorSkillSpec(
        name="intrude_engaged_event",
        description=(
            "强行引入某个 engaged 事件。"
            "本 skill 用于把事件方正在做的事，强行以直接影响的形式落成于主角，让主角从未接触状态转变为接触状态。"
            "必须提供 arguments.actors：列出本轮出场的事件演员姓名，或用 [] 明确不安排 NPC；未列出的演员不会自动入场。"
            "严禁以‘事件方尚未直接影响主角’为理由拒绝调用，如果类似语句出现在reasoning中，视为非法输出"
            "此 skill 是 engaged 事件开始作用于主角的唯一途径，因此只要时机合适，你必须进行事件引入。"
        ),
        phases=_GENERATION,
        mutates=False,
        arguments_model=IntrudeEngagedEventArguments,
        max_requests_per_batch=1,
    ),
    DirectorSkillSpec(
        name="introduce_npc",
        description=(
            "将一名新 NPC 首次引入本轮场景。适用场景：你规划该角色在场景中初次出现（登场或提及）。"
            "优先使用 resources.drafts 中的 npc_id 起用准备好的草稿；"
            "仅当 drafts 中无任何匹配当前场景的人物时，才允许使用 spontaneous 即兴提供轻量人物卡。"
            "resources.npcs 中的已登场 NPC 禁止再引入。"
            "view_mode 是该 NPC 首次披露授权：起用草稿时可选 remote/face_to_face/intimate；"
            "即兴新建（spontaneous）必须且只能使用 face_to_face（初次当面接触）。"
        ),
        phases=_GENERATION,
        mutates=False,
        arguments_model=IntroduceNPCArguments,
        max_requests_per_batch=5,
        input_targets={
            ("spontaneous", "name"): 40,
            ("spontaneous", "personality_trait"): 40,
            ("spontaneous", "known_abilities", "name"): 20,
            ("spontaneous", "known_abilities", "description"): 120,
        },
    ),
    DirectorSkillSpec(
        name="upgrade_disclosure",
        description=(
            "提升一个已存在 NPC 档案的披露等级（只能升不能降），使该 NPC 在本回合和未来"
            "按新等级向 Composer 展示更深入的信息（如从 face_to_face 提到 intimate 后，"
            "核心动机与隐藏秘密本回合即可用于正文）。"
            "只作用于 resources.npcs 中的档案；"
        ),
        phases=_GENERATION,
        mutates=False,
        arguments_model=UpgradeDisclosureArguments,
    ),
    DirectorSkillSpec(
        name="create_event",
        description=(
            "让第三方按自身目的行动，触及主角附近的人与目标，带来可回应的请求与机会；"
            "玩家保有选择。"
            "guidance 写清谁想做什么、对哪个对象采取什么行动、带来什么变化；"
            "背景与因果由 Selector 结合 Setting 补全。"
            "actors 至少一名真实演员：已有 NPC 用 source='existing' 填 npc_id，"
            "新人物用 source='new'；组织名不能替代演员，禁止无人物事件。"
            "setting_requirements 声明 1 个依托 Setting：命中时只能选 "
            "event_creation_available=true 的 matched_setting_id，否则留空。"
        ),
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=CreateEventArguments,
        max_requests_per_batch=1,
    ),
    DirectorSkillSpec(
        name="create_npc",
        description=(
            "基于未来可能的剧情需求，在幕后招募一名新的导演 NPC 草案。"
            "在 arguments.setting_requirements 中声明该人物真实生活依托的 1 个 Setting："
            "命中已有 Setting 时填其 matched_setting_id，否则该项留空。"
            "在 guidance 中，name 填写你要为该角色设定的专有姓名；purpose 填写其系统功能或剧情目的（50字以内）；"
            "description 填写背景，性格和设想。"
        ),
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=CreateNPCArguments,
    ),
    DirectorSkillSpec(
        name="bind_event_actor",
        description=(
            "为活跃事件分配具体的‘演员’。通过将现有 NPC 绑定到某事件中，使其成为该事件的执行者、受害者或关键人物。"
        ),
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=BindEventActorArguments,
    ),
)


def get_director_skill_specs() -> tuple[DirectorSkillSpec, ...]:
    """返回全部技能语义规则（新增条目时应同时补充本函数返回值）。"""
    return _DIRECTOR_SKILL_SPECS


def register_skill(
    service,
    name: str,
    *,
    handler,
    arguments_validator=None,
    batch_validator=None,
) -> None:
    """Register a skill using its semantic spec declared in this module."""
    spec = next(
        (item for item in _DIRECTOR_SKILL_SPECS if item.name == name),
        None,
    )
    if spec is None:
        raise ValueError(f"unknown director skill spec: {name}")
    service.register(
        name=spec.name,
        description=spec.description,
        phases=set(spec.phases),
        mutates=spec.mutates,
        arguments_model=spec.arguments_model,
        handler=handler,
        arguments_validator=arguments_validator,
        max_requests_per_batch=spec.max_requests_per_batch,
        batch_validator=batch_validator,
        input_targets=spec.input_targets,
    )
