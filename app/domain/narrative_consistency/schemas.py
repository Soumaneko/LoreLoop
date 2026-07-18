from typing import Literal

from pydantic import BaseModel, Field, model_validator


ConsistencyDecision = Literal["pass", "patch", "reject"]
ReviewVerdict = Literal["pass", "issue"]
ConsistencyIssueType = Literal[
    "logic_contradiction",
    "contract_violation",
    "player_agency_violation",
    "knowledge_boundary_violation",
    "continuity_contradiction",
    "stopping_point_violation",
    "causal_incoherence",
    "other",
]


class NarrativeConsistencyIssue(BaseModel):
    issue_type: ConsistencyIssueType
    paragraph_ids: list[str] = Field(default_factory=list, max_length=6)
    explanation: str = Field(min_length=1)


class SegmentReview(BaseModel):
    segment_id: str = Field(pattern=r"^S\d{3}$")
    paragraph_ids: list[str] = Field(min_length=1)
    audit_reasoning: str = Field(
        min_length=1,
        description=(
            "一条简短、可核验的三账本核对结论，建议格式为："
            "正文事实：...；PLAYER_INPUT：...；TR：...；玩家决定：...。"
            "必须分别说明正文实际事件、玩家硬参数是否一致或不涉及、"
            "TR最终状态是否一致或不涉及、是否新增玩家决定。"
        ),
    )
    verdict: ReviewVerdict
    contract_refs: list[str] = Field(default_factory=list, max_length=12)
    issues: list[NarrativeConsistencyIssue] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _validate_verdict(self):
        if self.verdict == "pass" and self.issues:
            raise ValueError("passing segment review must not contain issues")
        if self.verdict == "issue" and not self.issues:
            raise ValueError("issue segment review requires at least one issue")
        return self


class CrossSegmentReview(BaseModel):
    verdict: ReviewVerdict
    summary: str = Field(
        min_length=1,
        description=(
            "综合全部分段核对结果，只说明跨段后才显现的遗漏、反转、"
            "矛盾、越权累积或因果断裂。"
        ),
    )
    issues: list[NarrativeConsistencyIssue] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _validate_verdict(self):
        if self.verdict == "pass" and self.issues:
            raise ValueError("passing cross-segment review must not contain issues")
        if self.verdict == "issue" and not self.issues:
            raise ValueError("issue cross-segment review requires at least one issue")
        return self


class ParagraphPatch(BaseModel):
    start_paragraph_id: str = Field(pattern=r"^P\d{3}$")
    end_paragraph_id: str = Field(pattern=r"^P\d{3}$")
    replacement: str


class NarrativeConsistencyReview(BaseModel):
    segment_reviews: list[SegmentReview] = Field(min_length=1)
    cross_segment_review: CrossSegmentReview
    decision: ConsistencyDecision
    summary: str = Field(min_length=1)
    patches: list[ParagraphPatch] = Field(default_factory=list, max_length=3)

    @property
    def all_issues(self) -> list[NarrativeConsistencyIssue]:
        return [
            issue
            for segment in self.segment_reviews
            for issue in segment.issues
        ] + list(self.cross_segment_review.issues)

    @model_validator(mode="after")
    def _validate_decision_payload(self):
        issues = self.all_issues
        if self.decision == "pass" and (issues or self.patches):
            raise ValueError("pass must not contain issues or patches")
        if self.decision == "patch" and (not issues or not self.patches):
            raise ValueError("patch requires at least one issue and one patch")
        if self.decision == "reject" and (not issues or self.patches):
            raise ValueError("reject requires issues and must not contain patches")
        return self


class NarrativeConsistencyOutcome(BaseModel):
    review: NarrativeConsistencyReview
    scene_text: str = Field(min_length=1)
    changed_paragraph_ids: list[str] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changed_paragraph_ids)
