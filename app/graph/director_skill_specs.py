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
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.domain.director.schemas import DirectorPhase
from app.domain.director_event.schemas import DirectorEventRole


# ---------------------------------------------------------------------------
# 参数模型
# ---------------------------------------------------------------------------


NPCViewMode = Literal["remote", "face_to_face", "intimate"]

_VIEW_IDENTITY_FIELDS = frozenset({"name", "role", "aliases", "faction"})
_VIEW_REMOTE_FIELDS = _VIEW_IDENTITY_FIELDS | frozenset(
    {"public_summary", "social_style", "attitude_to_player"}
)
_VIEW_FACE_TO_FACE_FIELDS = _VIEW_REMOTE_FIELDS | frozenset(
    {"appearance", "personality_traits", "conflict_style", "known_abilities"}
)

#: view_mode -> 允许下发的字段白名单；None 表示全量下发（intimate）。
NPC_VIEW_MODE_FIELDS: dict[NPCViewMode, frozenset[str] | None] = {
    "remote": _VIEW_REMOTE_FIELDS,
    "face_to_face": _VIEW_FACE_TO_FACE_FIELDS,
    "intimate": None,
}

#: 无论何种 view_mode 都必须保留的身份/来源键。
NPC_VIEW_ALWAYS_KEPT = frozenset({"source", "npc_id", "draft_id", "name"})


class PresentEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    hook_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description=(
            "潜伏期(hidden)事件必须指定要呈现给 Composer 的 hook_id；"
            "不指定时不呈现任何内容。engaged 事件忽略该字段。"
        ),
    )


class IntroduceNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    view_mode: NPCViewMode = Field(
        description=(
            "选择引入NPC的视图模式。远程/间接接触选 remote（仅下发身份与传闻信息，"
            "不含外貌）；初次见面/当面战斗选 face_to_face（额外下发外貌与性格，"
            "但不含秘密）；深度交心或搜魂选 intimate（全量下发，含核心动机与隐藏秘密）。"
        )
    )


class ForeshadowSettingArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_id: str = Field(min_length=1)


class ReviewEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    consume_hooks: list[str] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "本轮确认后应标记为已消费的 hook_id 列表（仅消费该事件尚未消费的 hook）。"
            "hook 属于消耗型引子，被采用/呈现后必须在此声明，防止后续回合复读。"
        ),
    )


class CreateNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchors: list[str] = Field(default_factory=list, max_length=8)


class CreateCoupledSceneArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: DirectorEventRole
    npc_count: int = Field(ge=1, le=3)
    event_guidance: str = Field(default="", max_length=500)
    npc_guidances: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def _npc_guidances_fit_count(self):
        if len(self.npc_guidances) > self.npc_count:
            raise ValueError("npc_guidances must not exceed npc_count")
        return self


class EnrichSettingArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_id: str = Field(min_length=1)


class BindEventActorArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    npc_id: str = Field(min_length=1)


class EnrichNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


_GENERATION: frozenset[DirectorPhase] = frozenset({"generation"})
_CONFIRMATION: frozenset[DirectorPhase] = frozenset({"confirmation"})


_DIRECTOR_SKILL_SPECS: tuple[DirectorSkillSpec, ...] = (
    DirectorSkillSpec(
        name="present_event",
        description=(
            "将一个已存在的活跃事件注入本轮上下文。适用场景："
            "1.玩家行动直接干预了该事件，或事件后果在此刻爆发。"
            "2.主角通过传闻、异象、信件、远程通讯或现场遗留痕迹，间接接触到了与该事件相关的信息。"
            "通过引入事件，确保正文生成引擎能够准确描写出符合该事件发展进度的台词或环境暗示。"
            "对潜伏期(hidden)事件，必须用 arguments.hook_id 选择本轮要呈现的 Hook；"
            "不指定 hook_id 时不呈现任何内容。必须在请求级 guidance 中说明具体表现方式。"
        ),
        phases=_GENERATION,
        mutates=False,
        arguments_model=PresentEventArguments,
    ),
    DirectorSkillSpec(
        name="introduce_npc",
        description=(
            "将一名已存在的NPC注入本轮上下文，供正文生成引擎参考其详细设定。适用场景："
            "1.你规划NPC在本场景中出现。"
            "2.你规划文中需要描述该角色，需要参考其详细设定。"
            "注意：只要本轮叙事需要用到该角色的身份信息，就必须将其引入。"
            "必须根据本轮交互情境选择 view_mode（remote 远程/间接接触、face_to_face 初次见面/"
            "当面战斗、intimate 深度交心/搜魂），并在请求级 guidance 中说明使用方式；"
            "后端将按 view_mode 白名单物理隔离敏感字段。"
        ),
        phases=_GENERATION,
        mutates=False,
        arguments_model=IntroduceNPCArguments,
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
        name="review_event",
        description=(
            "根据本轮已确认的场景事实审查一个活跃导演事件，并掌握该事件的所有写库判定："
            "1. 阶段判定：事件核心冲突仍在持续时保持追踪(hold，hidden 转入 engaged)，"
            "已形成稳定结果时走向终结(resolve)。"
            "2. Hook 消费：本回合采用或呈现了该事件的哪个 hook，就在 consume_hooks 中声明，"
            "程序会将其标记为已消费防止复读。只有确认端的事件审查才能消费 hook 或推进阶段。"
        ),
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=ReviewEventArguments,
    ),
    DirectorSkillSpec(
        name="create_coupled_scene",
        description=(
            "创建一个长线事件（arc）或环境事件（ambient），并同时孕育若干个与该事件"
            "互相耦合的新 NPC，将其全部绑定为该事件的演员。这是创建事件的唯一技能，"
            "事件总是伴随至少一个新 NPC。"
            "只用于世界在主角视线之外的平行动态，"
            "不得用于推动、包装或代劳主角当前正在执行的主线行动。"
            "方向请通过 arguments 中的 event_guidance 与 npc_guidances 分别给出"
            "（npc_guidances 数量不得超过 npc_count，缺省时用请求级 guidance 兜底）。"
        ),
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=CreateCoupledSceneArguments,
    ),
    DirectorSkillSpec(
        name="create_npc",
        description="基于未来可能的剧情需求，在幕后‘招募’并捏造一名新的导演 NPC 草案（处于 prepared 状态）。该角色将作为你未来干预场景的备用棋子。",
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=CreateNPCArguments,
    ),
    DirectorSkillSpec(
        name="enrich_setting",
        description="将一个粗糙的‘设定种子 (Setting seed)’扩写并孵化为内容丰满的世界设定资产，为后续的场景渲染和事件铺垫提供具体支撑。",
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=EnrichSettingArguments,
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
            "为本轮新出现、档案尚不完整（缺少幕后动机/秘密/冲突/社交设定）的非导演 NPC "
            "补全完整人设并规范其姓名；若其当前 name 只是身份代称、职业泛称或外观代称，"
            "必须为其创造符合世界观的专有姓名。只用于本轮新入库、仍需补全的 NPC，"
            "每轮至多处理三个。"
        ),
        phases=_CONFIRMATION,
        mutates=True,
        arguments_model=EnrichNPCArguments,
    ),
)


def get_director_skill_specs() -> tuple[DirectorSkillSpec, ...]:
    """返回全部技能语义规则（新增条目时应同时补充本函数返回值）。"""
    return _DIRECTOR_SKILL_SPECS
