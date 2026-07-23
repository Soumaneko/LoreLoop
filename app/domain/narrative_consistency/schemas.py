from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ConsistencyDecision = Literal["pass", "revise"]
CanonFindingVerdict = Literal[
    "contradiction",
    "explicitly_confirmed",
    "insufficient",
]
ConsistencyIssueType = Literal[
    "logic_contradiction",
    "contract_violation",
    "player_agency_violation",
    "knowledge_boundary_violation",
    "continuity_contradiction",
    "stopping_point_violation",
    "causal_incoherence",
    "canon_contradiction",
    "other",
]
ConsistencyWarningReason = Literal[
    "missing_query_id",
    "unknown_query_id",
    "non_contradiction_finding",
    "missing_evidence",
    "unknown_evidence",
]


class NarrativeConsistencyAssertionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: ConsistencyIssueType
    paragraph_ids: list[str] = Field(min_length=1, max_length=8)
    incorrect_statement: str = Field(
        min_length=1,
        description="上一次正文实际建立的错误事实，保留主体、对象、方向、时间和状态。",
    )
    authoritative_fact: str = Field(
        min_length=1,
        description="权威输入实际确定、并与错误陈述不相容的正确事实或边界。",
    )
    required_revision: str = Field(
        min_length=1,
        description="Composer必须删除或改正的语义范围，并明确保留无关内容。",
    )
    authority_refs: list[str] = Field(min_length=1, max_length=8)
    canon_query_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class NarrativeConsistencyAssertion(NarrativeConsistencyAssertionDraft):
    @model_validator(mode="after")
    def _validate_canon_references(self):
        if self.issue_type == "canon_contradiction":
            if not self.canon_query_id or not self.evidence_ids:
                raise ValueError(
                    "canon_contradiction requires canon_query_id and evidence_ids"
                )
        elif self.canon_query_id is not None or self.evidence_ids:
            raise ValueError(
                "only canon_contradiction may cite canon query or evidence IDs"
            )
        return self


class NarrativeCanonFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1)
    verdict: CanonFindingVerdict
    reasoning: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class NarrativeConsistencyReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_summary: str = Field(min_length=1)
    canon_findings: list[NarrativeCanonFinding] = Field(max_length=12)
    decision: ConsistencyDecision
    assertions: list[NarrativeConsistencyAssertionDraft] = Field(
        default_factory=list,
        max_length=6,
    )


class NarrativeConsistencyReview(NarrativeConsistencyReviewDraft):
    assertions: list[NarrativeConsistencyAssertion] = Field(
        default_factory=list,
        max_length=6,
    )

    @model_validator(mode="after")
    def _validate_decision_payload(self):
        if self.decision == "pass" and self.assertions:
            raise ValueError("pass must not contain assertions")
        if self.decision == "revise" and not self.assertions:
            raise ValueError("revise requires at least one assertion")
        return self


class NarrativeConsistencyWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["ignored_canon_assertion"] = "ignored_canon_assertion"
    assertion_index: int = Field(ge=1)
    canon_query_id: str | None = None
    reason: ConsistencyWarningReason


def render_revision_instruction(review: NarrativeConsistencyReview) -> str:
    if review.decision != "revise":
        raise ValueError("only a revise review can be rendered for Composer")
    blocks = []
    for index, assertion in enumerate(review.assertions, start=1):
        blocks.append(
            f"问题 {index}\n"
            f"正文错误：{assertion.incorrect_statement.strip()}\n"
            f"正确事实：{assertion.authoritative_fact.strip()}\n"
            f"修订要求：{assertion.required_revision.strip()}"
        )
    return "\n\n".join(blocks)
