from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DirectorPhase = Literal["generation", "confirmation"]
DirectorResourceKind = Literal["event", "npc", "setting"]
MAX_DIRECTOR_SKILL_REQUESTS = 6


class DirectorObservation(BaseModel):
    """Phase-neutral input assembled for one Director decision."""

    model_config = ConfigDict(extra="forbid")

    phase: DirectorPhase
    turn_index: int = Field(ge=0)
    player_input: str = ""
    core_memory: dict[str, Any]
    current_state: dict[str, Any]
    scene_facts: dict[str, Any] | None = None
    context_facts: list[str] = Field(default_factory=list)
    recent_summaries: list[dict[str, Any]] = Field(default_factory=list)
    resources: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class DirectorSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=80)
    skill: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class DirectorDecisionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(min_length=1, max_length=500)
    requests: list[DirectorSkillRequest] = Field(
        default_factory=list,
        max_length=MAX_DIRECTOR_SKILL_REQUESTS,
    )

    @model_validator(mode="after")
    def _request_ids_are_unique(self):
        request_ids = [request.request_id for request in self.requests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("director request_id must be unique within one batch")
        return self


class DirectorSkillSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    phases: list[DirectorPhase] = Field(min_length=1)
    mutates: bool
    input_schema: dict[str, Any]


class DirectorSkillExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    skill: str
    output: dict[str, Any] = Field(default_factory=dict)


class DirectorSkillExecutionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executions: list[DirectorSkillExecution] = Field(default_factory=list)


class DirectorStageMaterial(BaseModel):
    """One read-only Director resource admitted to narrative composition."""

    model_config = ConfigDict(extra="forbid")

    kind: DirectorResourceKind
    resource_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)
    payload: dict[str, Any]


class DirectorResourceRef(BaseModel):
    """Optional cross-resource link owned by the Director coordinator."""

    model_config = ConfigDict(extra="forbid")

    kind: DirectorResourceKind
    resource_id: str = Field(min_length=1)


class DirectorStageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(min_length=1, max_length=500)
    materials: list[DirectorStageMaterial] = Field(
        default_factory=list,
        max_length=MAX_DIRECTOR_SKILL_REQUESTS,
    )
