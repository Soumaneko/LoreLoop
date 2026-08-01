from __future__ import annotations

import random

from .schemas import DirectorNPCCreativeBrief


_WORLD_CONNECTIONS = (
    "扎根于某个具体地方或社区",
    "依靠一个组织或制度谋生",
    "受到长期世界冲突的日常影响",
    "维护一条资源、知识或交通网络",
    "继承某段历史留下的现实后果",
    "在主流秩序边缘寻找生存空间",
    "利用某种普遍规则或技术生活",
)

_SOCIAL_POSITIONS = (
    "权力圈边缘",
    "制度执行层",
    "专业技能核心",
    "基础执行位置",
    "非正式影响者",
    "社会边缘位置",
    "被多人依赖但无名望",
)

_LIFE_PHASES = (
    "刚进入新阶段",
    "正在获得上升机会",
    "维持已有生活",
    "隐藏逐渐恶化的问题",
    "开始失去原有位置",
    "准备主动离开",
    "被迫重新开始",
    "多年后回到旧地方",
)

_DESIRE_PATTERNS = (
    "获得未拥有的生活",
    "保护正在失去的东西",
    "摆脱长期束缚",
    "证明自己的价值",
    "找到真正归属",
    "理解困扰自己的问题",
    "补偿过去错误",
    "与过去和解",
    "隐藏真实目的",
    "害怕失去重要关系",
)

_OPTIONAL_DIMENSIONS = (
    "依靠生产或维护维持生计",
    "沿固定路线或区域活动",
    "承担照料、服务或协调工作",
    "依靠交易、教学或研究生活",
    "处于温暖日常与现实压力之间",
    "面对潜在危险但没有特殊豁免",
)


def draw_director_npc_creative_brief(
    *,
    rng: random.Random | None = None,
) -> DirectorNPCCreativeBrief:
    """随机抽取人物灵魂底色与内驱力维度。

    只抽取纯内在属性（世界羁绊、人生阶段、欲望模式）；带强社会阶层属性的
    ``social_position`` 与 ``optional_dimension`` 不再进入 NPC 创建调用，
    以免与导演协调层 ``guidance`` 指定的身份冲突。字段保留以便未来按需复用。
    """
    random_source = rng or random.SystemRandom()
    return DirectorNPCCreativeBrief(
        world_connection=random_source.choice(_WORLD_CONNECTIONS),
        social_position=None,
        life_phase=random_source.choice(_LIFE_PHASES),
        desire_pattern=random_source.choice(_DESIRE_PATTERNS),
        optional_dimension=None,
    )
