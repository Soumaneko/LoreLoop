from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = Field(min_length=2, max_length=4)
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
