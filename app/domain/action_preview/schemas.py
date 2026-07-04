from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ActionPreviewLevel = Literal["冒险举动", "保守举动", "信息获取", "奇招"]


class ActionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: ActionPreviewLevel
    label: str = Field(min_length=1)
    good_result: str = Field(min_length=1)
    risk: str = Field(min_length=1)


class ActionPreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previews: list[ActionPreview] = Field(default_factory=list)

    @field_validator("previews")
    @classmethod
    def _validate_preview_count(cls, previews: list[ActionPreview]):
        if len(previews) != 4:
            raise ValueError("previews must contain exactly 4 items")
        return previews

    @field_validator("previews")
    @classmethod
    def _validate_unique_labels(cls, previews: list[ActionPreview]):
        seen_labels: set[str] = set()
        for preview in previews:
            label_key = preview.label.strip()
            if label_key in seen_labels:
                raise ValueError("previews must contain exactly 4 unique labels")
            seen_labels.add(label_key)
        return previews
