from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level: str = Field(min_length=2, max_length=4)
    label: str = Field(min_length=1)
    good_result: str = Field(min_length=1)
    risk: str = Field(min_length=1)


class ActionPreviewResult(BaseModel):
    """内部/持久化结果：降级路径允许 0–4 条，label 唯一。"""

    model_config = ConfigDict(extra="forbid")

    previews: list[ActionPreview] = Field(default_factory=list, max_length=4)

    @field_validator("previews")
    @classmethod
    def _validate_unique_labels(cls, previews: list[ActionPreview]):
        seen_labels: set[str] = set()
        for preview in previews:
            label_key = preview.label.strip()
            if label_key in seen_labels:
                raise ValueError("previews must contain unique labels")
            seen_labels.add(label_key)
        return previews


class ActionPreviewResultLoose(BaseModel):
    """Gateway 宽松容器：顶层 extra 不连坐，previews 由 output_validator 逐条解析。"""

    model_config = ConfigDict(extra="allow")

    previews: list[Any] = Field(default_factory=list)


class ActionPreviewResultDisplay(BaseModel):
    """prompt 展示 schema：正常路径必须正好 4 条且 label 唯一。"""

    model_config = ConfigDict(extra="forbid")

    previews: list[ActionPreview] = Field(min_length=4, max_length=4)

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
