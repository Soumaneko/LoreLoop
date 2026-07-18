from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CanonClaimType = Literal[
    "past_event",
    "knowledge_provenance",
    "causal_motivation",
    "identity_or_relationship",
    "ownership_or_resource",
    "location_or_presence",
    "world_rule_or_history",
    "persistent_state",
    "other",
]
CanonQueryPriority = Literal["high", "medium", "low"]
CanonEntityKind = Literal["player", "npc", "item", "location", "organization"]


class CanonEntityReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    kind: CanonEntityKind


class NarrativeCanonQueryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraph_ids: list[str] = Field(default_factory=list)
    claim: str = Field(min_length=1)
    claim_type: CanonClaimType
    entities: list[str] = Field(default_factory=list)
    lookup_queries: list[str] = Field(default_factory=list)
    priority: CanonQueryPriority


class NarrativeCanonQueryDraftSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thought_process: str = Field(min_length=1)
    queries: list[NarrativeCanonQueryDraft] = Field(default_factory=list, max_length=12)


class NarrativeCanonQuery(NarrativeCanonQueryDraft):
    query_id: str = Field(min_length=1)


class NarrativeCanonQuerySet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thought_process: str = Field(min_length=1)
    queries: list[NarrativeCanonQuery] = Field(default_factory=list, max_length=12)

    @classmethod
    def from_drafts(
        cls,
        *,
        thought_process: str,
        drafts: list[NarrativeCanonQueryDraft],
    ) -> "NarrativeCanonQuerySet":
        return cls(
            thought_process=thought_process,
            queries=[
                NarrativeCanonQuery(
                    query_id=f"q{index}",
                    **NarrativeCanonQueryDraft.model_validate(draft).model_dump(),
                )
                for index, draft in enumerate(drafts, start=1)
            ]
        )
