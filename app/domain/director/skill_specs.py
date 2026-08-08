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

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.domain.director.schemas import DirectorPhase
from app.domain.director_event.schemas import DirectorEventRole
from app.domain.npc_profile.schemas import NPCDisclosureLevel
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


class IntrudeEngagedEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)


class IntroduceNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    view_mode: NPCViewMode = Field(
        description=(
            "选择该导演 NPC 首次披露的等级。远程/间接接触选 remote；"
            "初次见面/当面选 face_to_face；深度交心或搜魂选 intimate。"
        )
    )


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


class ForeshadowSettingArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_id: str = Field(min_length=1)


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

    role: DirectorEventRole
    setting_requirements: list[SettingRequirement] = Field(
        min_length=1,
        max_length=1,
    )
    actors: list[EventActorInput] = Field(default_factory=list, max_length=6)

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


class EnrichNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    setting_requirements: list[SettingRequirement] = Field(
        min_length=1,
        max_length=1,
    )


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
            "将一名仍未登场的导演 NPC 草稿首次引入本轮场景。适用场景："
            "你规划该草稿在本场景中初次出现（登场或提及）。"
            "注意：只对 resources.drafts 中的 导演 NPC 草稿有效；"
            "resources.npcs 中的已登场 NPC 禁止再引入。"
            "view_mode 是该 NPC 首次披露授权：远程/间接接触选 remote，初次见面/当面选 "
            "face_to_face，深度交心或搜魂等选 intimate。"
        ),
        phases=_GENERATION,
        mutates=False,
        arguments_model=IntroduceNPCArguments,
        max_requests_per_batch=5,
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
        name="foreshadow_setting",
        description=(
            "将一个已存在的设定资产（如特殊地点、派系、传闻、功法等）注入本轮上下文供正文生成引擎提供细节支撑。"
            "要求底层引擎在正文中通过隐晦的可见影响、气氛渲染或主角的心理活动来暗示其存在。"
            "必须在请求级 guidance 中说明具体表现方式。"
        ),
        phases=_GENERATION,
        mutates=False,
        arguments_model=ForeshadowSettingArguments,
    ),
    DirectorSkillSpec(
        name="create_event",
        description=(
            "创建一个长线事件（arc）或环境事件（ambient）。这是创建事件的唯一技能。"
            "只用于主角视线之外的世界动态，不得用于推动、包装或代劳主角当前正在执行的主线行动。"
            "actors 是强制演员清单（existing/new 可混合，也可留空）。"
            "在 arguments.setting_requirements 中声明该事件因果依托的 1 个 Setting："
            "命中已有 Setting 时填其 matched_setting_id，否则该项留空。"
            "请求级 guidance 用自由文本指定事件方向。"
        ),
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=CreateEventArguments,
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
    DirectorSkillSpec(
        name="enrich_npc",
        description=(
            "为NPC补全完整人设并规范其姓名；若其当前 name 只是身份代称、职业泛称或外观代称，"
            "必须为其创造符合世界观的专有姓名。只用于本轮新入库、仍需补全的 NPC。"
            "在 arguments.setting_requirements 中声明该人物真实生活依托的 1 个 Setting："
            "命中已有 Setting 时填其 matched_setting_id，否则该项留空。每轮至多处理三个。"
            "在 guidance 中，name 填写你要为该角色设定的专有姓名；purpose 填写其剧情目的（50字以内）；"
            "description 填写背景，性格和设想。"
        ),
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=EnrichNPCArguments,
        max_requests_per_batch=3,
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
    )
