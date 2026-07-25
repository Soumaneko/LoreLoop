from __future__ import annotations

import random

from app.domain.world_setting.schemas import SettingKind

from .schemas import DirectorNPCCreativeBrief


_RELATIONS: dict[SettingKind, tuple[str, ...]] = {
    "place": (
        "扎根于此地的人",
        "反复往返此地的人",
        "依靠此地谋生的人",
        "试图离开此地的人",
        "与此地形成深层绑定的人",
        "维护此地秩序的人",
        "依靠此地规则获益的人",
    ),
    "organization": (
        "组织中的服从者",
        "组织中的执行者",
        "组织中的受益者",
        "组织中的改革者",
        "组织中的逃离者",
        "组织的外部依附者",
        "与组织目标存在冲突的人",
    ),
    "historical_event": (
        "事件亲历者",
        "亲历者后代",
        "事件获益者",
        "事件受害者",
        "承担长期遗留问题者",
        "靠事件遗产生活者",
        "维护官方记忆者",
        "保存被遗忘版本者",
    ),
    "institution_or_custom": (
        "高度认同规则者",
        "机械遵守者",
        "维护规则者",
        "从规则获利者",
        "被规则伤害者",
        "利用规则边界生存者",
        "挑战规则者",
    ),
    "phenomenon_or_rule": (
        "长期受影响者",
        "观察记录者",
        "研究解释者",
        "利用规律谋生者",
        "误解规律导致受损者",
        "处理规律副作用者",
        "试图控制规律者",
    ),
    "knowledge_or_tradition": (
        "核心传承者",
        "边缘学习者",
        "实用使用者",
        "商业利用者",
        "拒绝继承者",
        "普通接触者",
    ),
    "resource_or_network": (
        "末端使用者",
        "基层生产者",
        "中间协调者",
        "依赖其稳定供应者",
        "被网络排除者",
        "从断裂中获利者",
    ),
}

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

_DESIRE_PATTERN_GROUPS = {
    "DESIRE": (
        "获得未拥有的生活",
        "保护正在失去的东西",
        "摆脱长期束缚",
        "证明自己的价值",
        "找到真正归属",
        "理解困扰自己的问题",
        "掌控失控部分",
        "补偿过去错误",
        "完成一直想做的事情",
        "与过去和解",
    ),
    "FEAR": (
        "害怕被遗忘",
        "害怕失去价值",
        "害怕真相被发现",
        "害怕离开熟悉环境",
        "害怕无法改变",
        "害怕失去重要关系",
    ),
    "SECRET": (
        "隐藏真实身份",
        "隐藏过去错误",
        "隐藏真实目的",
        "隐藏特殊之处",
        "隐藏与某事件的关系",
        "隐藏自己的软弱",
        "隐藏自己其实想离开",
    ),
}

_OPTIONAL_DIMENSION_GROUPS = {
    "生计机制": (
        "生产",
        "运输",
        "维护",
        "照料",
        "管理",
        "执行规则",
        "交易",
        "展示",
        "教学",
        "研究",
        "服务",
        "中介",
    ),
    "活动范围": (
        "固定地点生活",
        "固定路线循环",
        "城市内部移动",
        "区域往返",
        "季节迁移",
        "长期漂泊",
        "被迫迁移",
    ),
    "叙事压力": (
        "平静日常",
        "温暖人情",
        "荒诞幽默",
        "苦涩现实",
        "潜在危险",
        "持续压迫",
        "灾难边缘",
    ),
}


def draw_director_npc_creative_brief(
    setting_kind: SettingKind,
    *,
    rng: random.Random | None = None,
) -> DirectorNPCCreativeBrief:
    random_source = rng or random.SystemRandom()
    return DirectorNPCCreativeBrief(
        setting_relation=random_source.choice(_RELATIONS[setting_kind]),
        social_position=random_source.choice(_SOCIAL_POSITIONS),
        life_phase=random_source.choice(_LIFE_PHASES),
        desire_pattern=_draw_grouped_choice(
            _DESIRE_PATTERN_GROUPS,
            rng=random_source,
        ),
        optional_dimension=_draw_grouped_choice(
            _OPTIONAL_DIMENSION_GROUPS,
            rng=random_source,
        ),
    )


def _draw_grouped_choice(
    groups: dict[str, tuple[str, ...]],
    *,
    rng: random.Random,
) -> str:
    group = rng.choice(tuple(groups))
    value = rng.choice(groups[group])
    return f"{group}：{value}"
