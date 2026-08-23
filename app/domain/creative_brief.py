from __future__ import annotations

import random

from pydantic import BaseModel, ConfigDict, Field


class CreativeBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    world_connection: str = Field(min_length=1)
    social_position: str | None = Field(default=None, min_length=1)
    life_phase: str = Field(min_length=1)
    desire_pattern: str = Field(min_length=1)
    baseline_temperament: str = Field(min_length=1)
    optional_dimension: str | None = Field(default=None, min_length=1)


_WORLD_CONNECTIONS = (
    "扎根于某个具体地方或社区",
    "依靠一个组织或制度谋生",
    "受到长期世界冲突的日常影响",
    "维护一条资源、知识或交通网络",
    "继承某段历史留下的现实后果",
    "在主流秩序边缘寻找生存空间",
    "利用某种普遍规则或技术生活",
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
    "保护即将失去的事物",
    "弥补自己犯下的错误",
    "将自己的意志与规则强加于人",
    "让伤害过自己的付出代价",
    "证明自己的价值",
    "斩断控制自己的锁链或宿命",
    "探寻真相或更高境界",
    "寻找能毫无保留接纳自己的避风港",
    "寻求刺激与秩序的破坏",
)

_BASELINE_TEMPERAMENTS = (
    "热情豪爽",
    "古灵精怪",
    "暴躁强势",
    "温婉平和",
    "极度务实",
    "高傲毒舌",
    "娇憨随和",
    "慵懒散漫",
    "冷漠孤僻",
)


def draw_creative_brief(
    *,
    rng: random.Random | None = None,
) -> CreativeBrief:
    """随机抽取人物灵魂底色、基准性情与内驱力维度。"""
    random_source = rng or random.SystemRandom()
    return CreativeBrief(
        world_connection=random_source.choice(_WORLD_CONNECTIONS),
        social_position=None,
        life_phase=random_source.choice(_LIFE_PHASES),
        desire_pattern=random_source.choice(_DESIRE_PATTERNS),
        baseline_temperament=random_source.choice(_BASELINE_TEMPERAMENTS),
        optional_dimension=None,
    )


def draw_desire_pattern(
    *,
    rng: random.Random | None = None,
) -> str:
    """仅抽取欲望模式字符串，用于即兴 NPC 补全，避免与既有性情/外貌冲突。"""
    random_source = rng or random.SystemRandom()
    return random_source.choice(_DESIRE_PATTERNS)
