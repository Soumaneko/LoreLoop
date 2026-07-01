from typing import Literal

from pydantic import BaseModel, Field

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

    actor: str
    action_type: ActionType

    raw_text: str
    verb: str
    target: str | None = None
    method: str | None = None
    goal: str | None = None
    condition: str | None = None

    likely_needs_roll: bool
    risk_level: ActionRiskLevel | None = None


class Anchor(BaseModel):
    text: str
    normalized: str
    kind: AnchorKind
    source: Literal["explicit", "implicit_from_scene"]
    confidence: float = Field(ge=0, le=1)


class ActionExtractionResult(BaseModel):
    analysis: str = ""
    turn_intent: str
    atomic_actions: list[AtomicAction] = Field(default_factory=list)


class AnchorExtractionResult(BaseModel):
    anchors: list[Anchor] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)


class ActionParseResult(BaseModel):
    analysis: str = ""
    turn_intent: str
    atomic_actions: list[AtomicAction] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
