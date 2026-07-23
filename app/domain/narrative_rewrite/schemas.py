from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RewriteStrategy = Literal[
    "revise_from_source",
    "recompose",
    "no_rewrite",
]
RewriteReasonCode = Literal[
    "localized_correction",
    "localized_preference",
    "causal_backbone_invalid",
    "no_actionable_request",
    "changes_player_action",
    "changes_resolved_outcome",
    "conflicts_with_authority",
    "unsupported_request",
]


class RewriteStrategyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: RewriteStrategy
    reason_code: RewriteReasonCode
    reasoning: str = Field(min_length=1)
    rewrite_requirements: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_strategy_payload(self):
        if self.strategy == "no_rewrite" and self.rewrite_requirements:
            raise ValueError("no_rewrite must not contain rewrite requirements")
        if self.strategy != "no_rewrite" and not self.rewrite_requirements:
            raise ValueError("a rewrite strategy requires rewrite requirements")
        allowed_reasons = {
            "revise_from_source": {
                "localized_correction",
                "localized_preference",
            },
            "recompose": {"causal_backbone_invalid"},
            "no_rewrite": {
                "no_actionable_request",
                "changes_player_action",
                "changes_resolved_outcome",
                "conflicts_with_authority",
                "unsupported_request",
            },
        }
        if self.reason_code not in allowed_reasons[self.strategy]:
            raise ValueError(
                f"reason_code {self.reason_code!r} does not match {self.strategy!r}"
            )
        return self


class SourceRewriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_scene_text: str = Field(min_length=1)
    requirements: list[str] = Field(min_length=1, max_length=8)


class RecomposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[str] = Field(min_length=1, max_length=8)
