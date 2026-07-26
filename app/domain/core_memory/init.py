from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import (
    CurrentState,
    PlayerState,
    ResourcesState,
    SceneState,
    WorldClock,
)


AbilitySystemPreset = Literal["无超自然", "低超自然", "魔法", "灵能", "科技奇迹", "自定义"]


class ProtagonistAbility(BaseModel):
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
    version: int = 1

    fanfic: bool = False
    work_name: str | None = None
    birth_location: str | None = None
    story_era: str | None = None

    world_name: str | None = None
    world_style: str | None = None
    ability_system: AbilitySystemPreset | None = None
    custom_ability_system: str | None = None

    protagonist_gender: Literal["男", "女", "其他"] = "男"
    protagonist_name: str = ""
    protagonist_age: int | None = Field(default=None, ge=0)
    protagonist_background: str | None = None

    protagonist_abilities: list[ProtagonistAbility] = Field(
        default_factory=list,
        max_length=3,
    )
    narrative_tone: str | None = None
    story_expectation: str | None = None

    extra_core_memory_items: list[CoreMemoryItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_required_fields(self):
        if not self.protagonist_name.strip():
            raise ValueError("protagonist_name is required")

        if self.fanfic:
            missing = [
                name
                for name in ["work_name", "birth_location", "story_era"]
                if not str(getattr(self, name) or "").strip()
            ]
            if missing:
                raise ValueError("fanfic config missing required fields: " + ", ".join(missing))
        else:
            missing = [
                name
                for name in ["world_name", "world_style"]
                if not str(getattr(self, name) or "").strip()
            ]
            if missing:
                raise ValueError("original config missing required fields: " + ", ".join(missing))

            if self.ability_system == "自定义" and not str(self.custom_ability_system or "").strip():
                raise ValueError("custom_ability_system is required when ability_system is 自定义")

        return self


def build_core_memory(config: InitConfig) -> CoreMemory:
    return CoreMemory(items=build_core_memory_items(config))


def build_initial_current_state(
    config: InitConfig,
    *,
    scene_id: str = "scene_001",
) -> CurrentState:
    protagonist_name = config.protagonist_name.strip()
    location = _initial_location(config)

    visible_parts = [f"{config.protagonist_gender}性"]
    if config.protagonist_age is not None:
        visible_parts.append(f"{config.protagonist_age}岁")

    return CurrentState(
        scene=SceneState(
            scene_id=scene_id,
            location=location,
            participants=[{"name": protagonist_name}],
        ),
        world_clock=WorldClock(day=1, phase="清晨"),
        player=PlayerState(
            name=protagonist_name,
            visible_status="，".join(visible_parts),
        ),
        resources=ResourcesState(
            inventory=[],
            money=None,
            fixed_assets=[],
            temporary_assets=[],
        ),
    )


def build_core_memory_items(config: InitConfig) -> list[CoreMemoryItem]:
    items = [
        _build_world_identity(config),
        _build_player_identity(config),
    ]

    if config.protagonist_abilities:
        items.append(CoreMemoryItem(
            id="core_player_abilities",
            category="player",
            content="主角的特殊能力：\n" + "\n".join(
                f"- {ability.name}：{ability.description}"
                for ability in config.protagonist_abilities
            ),
            priority=100,
        ))

    if config.narrative_tone and config.narrative_tone.strip():
        items.append(CoreMemoryItem(
            id="core_tone",
            category="tone",
            content=config.narrative_tone.strip(),
            priority=100,
        ))

    items.extend(config.extra_core_memory_items)
    return items


def _initial_location(config: InitConfig) -> str:
    if config.birth_location and config.birth_location.strip():
        return config.birth_location.strip()
    if config.world_name and config.world_name.strip():
        return config.world_name.strip()
    return "未知地点"


def _build_world_identity(config: InitConfig) -> CoreMemoryItem:
    if config.fanfic:
        content = (
            f"当前世界是同人世界，原作《{config.work_name}》。"
            f"故事出生点为{config.birth_location}，发生在{config.story_era}时代。\n"
            "所有剧情必须严格遵循原作世界观、人物性格和历史事件，不得违背原作设定。"
        )
    else:
        content = (
            f"当前世界名为「{config.world_name}」，"
            f"是一个{config.world_style}风格的原创世界。"
        )
        ability_system = _ability_system_label(config)
        if ability_system:
            content += f"能力体系为{ability_system}。"
    return CoreMemoryItem(
        id="core_world_identity",
        category="world",
        content=content,
        priority=100,
    )


def _build_player_identity(config: InitConfig) -> CoreMemoryItem:
    gender = f"{config.protagonist_gender}性"
    content = f"主角是{config.protagonist_name.strip()}，{gender}"
    if config.protagonist_age is not None:
        content += f"，{config.protagonist_age}岁"
    content += "。"
    if config.protagonist_background and config.protagonist_background.strip():
        content += f"\n背景：{config.protagonist_background.strip()}"

    return CoreMemoryItem(
        id="core_player_identity",
        category="player",
        content=content,
        priority=100,
    )


def _ability_system_label(config: InitConfig) -> str | None:
    if config.ability_system == "自定义":
        return (config.custom_ability_system or "").strip() or None
    if config.ability_system:
        return config.ability_system
    return None
