from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import (
    CurrentState,
    InventoryItem,
    PlayerState,
    ResourcesState,
    SceneState,
    WorldClock,
)

from .campaign_setup import CampaignSetupProposal


FailureSeverity = Literal["宽容", "适中", "真实残酷"]
AbilitySystemPreset = Literal[
    "无超自然",
    "低超自然",
    "魔法",
    "灵能",
    "科技奇迹",
    "自定义",
]


class ProtagonistAbility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean


class InitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[3] = 3
    director_enabled: bool = True

    world_name: str = Field(min_length=1, max_length=120)
    ability_system: AbilitySystemPreset
    custom_ability_system: str | None = Field(default=None, max_length=500)
    world_style: str = Field(min_length=1, max_length=1200)

    protagonist_name: str = Field(min_length=1, max_length=120)
    protagonist_gender: Literal["男", "女", "其他"] = "男"
    protagonist_age: int | None = Field(default=None, ge=0)
    protagonist_introduction: str | None = Field(default=None, max_length=1200)
    protagonist_abilities: list[ProtagonistAbility] = Field(
        default_factory=list,
        max_length=3,
    )

    story_expectation: str = Field(min_length=1, max_length=1200)
    failure_severity: FailureSeverity = "适中"

    @field_validator(
        "world_name",
        "world_style",
        "protagonist_name",
        "story_expectation",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean

    @field_validator("custom_ability_system", "protagonist_introduction")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        clean = (value or "").strip()
        return clean or None

    @model_validator(mode="after")
    def validate_custom_ability_system(self):
        if self.ability_system == "自定义" and not self.custom_ability_system:
            raise ValueError(
                "custom_ability_system is required when ability_system is 自定义"
            )
        if self.ability_system != "自定义" and self.custom_ability_system:
            raise ValueError(
                "custom_ability_system is only allowed when ability_system is 自定义"
            )
        return self


def build_identity_core_memory(config: InitConfig) -> CoreMemory:
    return CoreMemory(items=build_identity_core_memory_items(config))


def build_core_memory(
    config: InitConfig,
    proposal: CampaignSetupProposal,
) -> CoreMemory:
    return CoreMemory(items=[
        *build_identity_core_memory_items(config),
        *proposal.to_core_memory_items(),
    ])


def build_initial_current_state(
    config: InitConfig,
    *,
    location: str,
    currency_unit: str,
    inventory: list[InventoryItem] | None = None,
    money: int | None = None,
    fixed_assets: list[str] | None = None,
    scene_id: str = "scene_001",
) -> CurrentState:
    visible_parts = [f"{config.protagonist_gender}性"]
    if config.protagonist_age is not None:
        visible_parts.append(f"{config.protagonist_age}岁")

    return CurrentState(
        scene=SceneState(
            scene_id=scene_id,
            location=location.strip(),
            participants=[{"name": config.protagonist_name}],
        ),
        world_clock=WorldClock(day=1, phase="清晨"),
        player=PlayerState(
            name=config.protagonist_name,
            visible_status="，".join(visible_parts),
        ),
        resources=ResourcesState(
            inventory=inventory or [],
            money=money,
            money_unit=currency_unit.strip(),
            fixed_assets=fixed_assets or [],
            temporary_assets=[],
        ),
    )


def build_identity_core_memory_items(config: InitConfig) -> list[CoreMemoryItem]:
    items = [
        CoreMemoryItem(
            id="core_world_identity",
            kind="world_invariant",
            strength="hard_constraint",
            source="player",
            content=(
                f"当前世界名为「{config.world_name}」，"
                f"世界风格为：{config.world_style}\n"
                f"能力体系为：{_ability_system_label(config)}"
            ),
        ),
        _build_player_identity(config),
        CoreMemoryItem(
            id="core_story_expectation",
            kind="narrative_preference",
            strength="strong_preference",
            source="player",
            content=f"玩家期望的长期故事体验：{config.story_expectation}",
        ),
        CoreMemoryItem(
            id="core_failure_severity",
            kind="narrative_preference",
            strength="strong_preference",
            source="player",
            content=f"失败后果尺度：{config.failure_severity}",
        ),
    ]
    if config.protagonist_abilities:
        items.append(CoreMemoryItem(
            id="core_player_abilities",
            kind="ability_rule",
            strength="hard_constraint",
            source="player",
            content="主角的特殊能力：\n" + "\n".join(
                f"- {ability.name}：{ability.description}"
                for ability in config.protagonist_abilities
            ),
        ))
    return items


def _build_player_identity(config: InitConfig) -> CoreMemoryItem:
    content = f"主角是{config.protagonist_name}，{config.protagonist_gender}性"
    if config.protagonist_age is not None:
        content += f"，{config.protagonist_age}岁"
    content += "。"
    if config.protagonist_introduction:
        content += f"\n人物介绍：{config.protagonist_introduction}"
    return CoreMemoryItem(
        id="core_player_identity",
        kind="protagonist_invariant",
        strength="hard_constraint",
        source="player",
        content=content,
    )


def _ability_system_label(config: InitConfig) -> str:
    return config.custom_ability_system or config.ability_system
