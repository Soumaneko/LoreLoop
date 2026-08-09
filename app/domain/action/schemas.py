from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ActionType = Literal[
    "move",
    "observe",
    "inspect_object",
    "interact_object",
    "speak",
    "signal_ally",
    "instruct_ally",
    "hide_or_stealth",
    "attack",
    "defend",
    "cast_or_use_power",
    "wait",
    "other",
]

ActionRiskLevel = Literal["low", "medium", "high"]

AnchorKind = Literal[
    "character",
    "location",
    "item",
    "organization",
    "concept",
    "action_keyword",
    "unknown",
]


class AtomicAction(BaseModel):
    action_id: str
    sequence: int

    actor: list[str]
    action_type: ActionType

    raw_text: str
    verb: str
    target: str | None = None
    method: str | None = None
    condition: str | None = None

    likely_needs_roll: bool
    risk_level: ActionRiskLevel | None = None


class Anchor(BaseModel):
    text: str
    normalized: str
    kind: AnchorKind
    source: Literal["explicit", "implicit_from_scene"]
    confidence: float = Field(ge=0, le=1)


class ActionRetrievalTopic(BaseModel):
    """A compact semantic hint used for history retrieval."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=24)


class ActionExtractionResult(BaseModel):
    analysis: str = ""
    retrieval_topics: list[ActionRetrievalTopic] = Field(
        default_factory=list,
        max_length=8,
    )
    atomic_actions: list[AtomicAction] = Field(default_factory=list)
    touched_event_ids: list[str] = Field(default_factory=list)


class ActionParseResult(BaseModel):
    analysis: str = ""
    retrieval_topics: list[ActionRetrievalTopic] = Field(
        default_factory=list,
        max_length=8,
    )
    atomic_actions: list[AtomicAction] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    touched_event_ids: list[str] = Field(default_factory=list)
