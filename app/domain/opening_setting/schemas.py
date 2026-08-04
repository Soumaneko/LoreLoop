from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.world_setting.schemas import SettingKind


class OpeningSettingSpec(BaseModel):
    """指导 WorldSettingCreator 的规格——与导演侧 SettingRequirement 同构。

    每个 spec 只给出待固化设定的高层主题/范围（简短 kernel），
    具体事实与细节由 WorldSettingCreator 基于 CoreMemory 自行展开。
    opening_setting_node 把 ``setting_description`` 原样作为
    ``WorldSettingCreator.create(kind=..., guidance=setting_description)``
    的输入，与导演 agent 缺失设定补建走同一条链路。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: SettingKind
    setting_description: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "简短的高层主题/范围（如地点名+一句定位、势力名+一句立场、"
            "规则名+一条核心），描述 setting 本身而不是 npc/事件/细节预设"
        ),
    )


class OpeningSettingSpecs(BaseModel):
    """开局设定规格（2–4 条，种类由 agent 按世界需要自选）。"""

    model_config = ConfigDict(extra="forbid")

    specs: list[OpeningSettingSpec] = Field(min_length=2, max_length=4)
