from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.action.schemas import ActionType, AnchorKind

ActionPreviewLevel = Literal["冒险举动", "保守举动", "信息获取", "奇招"]


class ActionPreviewAnchor(BaseModel):
    text: str = Field(min_length=1)
    normalized: str | None = None
    kind: AnchorKind = "unknown"
    confidence: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def _fill_normalized(self):
        if self.normalized is None or not self.normalized.strip():
            self.normalized = self.text.strip()
        return self


class ActionPreview(BaseModel):
    action_id: str = Field(min_length=1)
    level: ActionPreviewLevel

    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    good_result: str = Field(min_length=1)
    risk: str = Field(min_length=1)

    action_type: ActionType
    verb: str = Field(min_length=1)
    target: str | None = None
    method: str | None = None
    goal: str | None = None
    likely_needs_roll: bool
    anchors: list[ActionPreviewAnchor] = Field(default_factory=list)


class ActionPreviewResult(BaseModel):
    scene_context: str = Field(min_length=1)
    previews: list[ActionPreview] = Field(default_factory=list)

    @field_validator("previews")
    @classmethod
    def _validate_preview_count(cls, previews: list[ActionPreview]):
        if len(previews) != 4:
            raise ValueError("previews must contain exactly 4 items")
        return previews

    @model_validator(mode="after")
    def _normalize_ids_and_deduplicate(self):
        seen_ids: set[str] = set()
        seen_labels: set[str] = set()
        normalized: list[ActionPreview] = []

        for index, preview in enumerate(self.previews, start=1):
            action_id = preview.action_id.strip() or f"ap_{index}"
            if action_id in seen_ids:
                action_id = f"ap_{index}"
            label_key = preview.label.strip()
            if label_key in seen_labels:
                continue

            seen_ids.add(action_id)
            seen_labels.add(label_key)
            if action_id != preview.action_id:
                preview = preview.model_copy(update={"action_id": action_id})
            normalized.append(preview)

        if len(normalized) != 4:
            raise ValueError("previews must contain exactly 4 unique labels")
        self.previews = normalized
        return self
