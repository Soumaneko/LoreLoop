"""Independent, save-scoped decks for the confirmation Director's Event ideas."""

import random

from pydantic import BaseModel, ConfigDict, Field, model_validator


EVENT_CREATIVE_POOLS = {
    "cause": {
        "cause_old_secret": "隐藏已久的秘密或旧账被翻出",
        "cause_disaster": "突发变故或意外灾祸打破了日常",
        "cause_deadline": "某项截止期限或关键节点即将到来",
        "cause_discovery": "无意中发现了禁忌真相或高价值之物",
        "cause_shortage": "维持现状的资源或渠道出现短缺",
        "cause_internal_conflict": "长期积累的内部矛盾终于公开爆发",
    },
    "opposition": {
        "opposition_companion": "亲近者或同伴",
        "opposition_rival": "竞争者或同行",
        "opposition_authority": "既有组织或权威",
        "opposition_affected_party": "利益受损者",
        "opposition_invader": "打破秩序的外敌",
        "opposition_uninformed_group": "不明真相的群体",
    },
    "tone": {
        "tone_urgent": "紧张急迫",
        "tone_eerie": "悬疑诡谲",
        "tone_farce": "荒诞乌龙",
        "tone_grim": "压抑沉重",
        "tone_lively": "热闹喧嚣",
    },
}


class EventCreativeDecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: list[str] = Field(default_factory=list)
    opposition: list[str] = Field(default_factory=list)
    tone: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_cards(self):
        for key, pool in EVENT_CREATIVE_POOLS.items():
            cards = getattr(self, key)
            if len(cards) != len(set(cards)) or not set(cards).issubset(pool):
                raise ValueError(f"invalid Event creative deck: {key}")
        return self


def draw_event_creative_brief(
    decks: EventCreativeDecks, *, rng: random.Random | None = None,
) -> tuple[dict[str, str], EventCreativeDecks]:
    random_source = rng or random.SystemRandom()
    remaining = decks.model_dump()
    brief = {}
    for key, pool in EVENT_CREATIVE_POOLS.items():
        if not remaining[key]:
            remaining[key] = list(pool)
            random_source.shuffle(remaining[key])
        card_id = remaining[key].pop(0)
        brief[key] = pool[card_id]
    return brief, EventCreativeDecks.model_validate(remaining)


def event_creative_brief_description(brief: dict[str, str]) -> str:
    return (
        "\n\n本次事件创作抽卡："
        f"\n事件起因：{brief['cause']}"
        f"\n阻力来源：{brief['opposition']}"
        f"\n事件基调：{brief['tone']}"
        "\n创建事件时，以【事件起因】触发第三方的行动，引入【阻力来源】与之产生正面摩擦或博弈，"
        "并通过具体的事态发展呈现出【事件基调】的氛围，合成一段连贯的 guidance。"
    )
