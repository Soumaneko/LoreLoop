from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DirectorPhase = Literal["generation", "confirmation"]
DirectorResourceKind = Literal["event", "npc", "setting", "engaged_event"]
MAX_DIRECTOR_SKILL_REQUESTS = 6

# 三级长度契约（H <= A <= S）：Prompt 目标 / 首轮免重试接收上限 /
# 重试耗尽后 soft_text 的最终保留上限。
DIRECTOR_REASONING_HARD_LIMIT = 500
DIRECTOR_REASONING_ACCEPT_LIMIT = 625
DIRECTOR_REASONING_SOFT_LIMIT = 750
GUIDANCE_NAME_HARD_LIMIT = 20
GUIDANCE_NAME_ACCEPT_LIMIT = 25
GUIDANCE_PURPOSE_HARD_LIMIT = 100
GUIDANCE_PURPOSE_ACCEPT_LIMIT = 125
GUIDANCE_PURPOSE_SOFT_LIMIT = 150


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
    recent_director_log: list[dict[str, Any]] = Field(default_factory=list)


class DirectorGuidance(BaseModel):
    """Structured guidance for character-oriented skills.

    ``description`` is a honeypot: the model may pour creative detail into it,
    but the backend discards it and only forwards ``name`` + ``purpose``.
    """

    model_config = ConfigDict(extra="forbid")

    # name: strict_name（无截断，接收上限 A）；purpose: soft_text（上限 S）。
    name: str = Field(min_length=1, max_length=25)
    purpose: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=150)


#: Free-form direction text (events, stage direction) OR structured guidance
#: (character creation/enrichment). Pydantic disambiguates on shape.
DirectorGuidanceInput = Annotated[
    str | DirectorGuidance,
    Field(
        description=(
            "自由文本方向（事件/舞台调度），或 {name, purpose, description} "
            "结构化人物指导（create_npc）。"
        )
    ),
]


class DirectorSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=80)
    skill: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    guidance: DirectorGuidanceInput


class DirectorDecisionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning: str = Field(
        min_length=1, max_length=DIRECTOR_REASONING_SOFT_LIMIT,
    )
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


class DirectorGuidanceWire(BaseModel):
    """宽松 wire：guidance 长度不设上限，由 agent 的 output_validator 分流。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    description: str = Field(min_length=1)


class DirectorSkillRequestWire(BaseModel):
    """request_id / skill 是 identifier，保持严格；仅自由文本字段放宽。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=80)
    skill: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    guidance: str | DirectorGuidanceWire


class DirectorDecisionBatchLoose(BaseModel):
    """Gateway 宽松容器：reasoning 可空、requests 由 output_validator 逐条解析。"""

    model_config = ConfigDict(extra="allow")

    reasoning: str | None = None
    requests: list[Any] = Field(default_factory=list)


class DirectorSkillSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    phases: list[DirectorPhase] = Field(min_length=1)
    mutates: bool
    input_schema: dict[str, Any]
    max_requests_per_batch: int | None = Field(default=None, ge=1)


class DirectorSkillExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    skill: str
    output: dict[str, Any] = Field(default_factory=dict)
    guidance: str | None = None


class DirectorSkillExecutionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executions: list[DirectorSkillExecution] = Field(default_factory=list)


class DirectorStageMaterial(BaseModel):
    """One read-only Director resource admitted to narrative composition."""

    model_config = ConfigDict(extra="forbid")

    kind: DirectorResourceKind
    resource_id: str = Field(min_length=1)
    guidance: str = Field(min_length=1, max_length=500)
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
