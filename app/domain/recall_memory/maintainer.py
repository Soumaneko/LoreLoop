import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.recall_memory.schemas import (
    EvidenceMode,
    Memory,
    MemoryKind,
    MemoryRecord,
)
from app.domain.recall_memory.store import MemoryStore
from app.domain.scene_summary.schemas import SceneSummary


MaintenanceAction = Literal[
    "keep",
    "invalidate",
    "supersede",
    "merge",
    "discard_summary_like",
]


class ReplacementMemory(BaseModel):
    memory_kind: MemoryKind
    evidence_mode: EvidenceMode
    subject: str = Field(min_length=1)
    content: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)
    owner: str | None = None
    visibility: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0, le=1)
    salience: float = Field(default=0.5, ge=0, le=1)
    location: str | None = None
    time_label: str | None = None


class MemoryMaintenanceProposal(BaseModel):
    reason: str = Field(min_length=1)
    evidence_summary_ids: list[str] = Field(default_factory=list)
    action: MaintenanceAction
    source_memory_ids: list[str] = Field(default_factory=list)
    replacement: ReplacementMemory | None = None

    @model_validator(mode="after")
    def _validate_action(self):
        if not self.source_memory_ids:
            raise ValueError("source_memory_ids is required")
        if self.action in {"supersede", "merge"} and self.replacement is None:
            raise ValueError(f"{self.action} requires replacement")
        if self.action not in {"supersede", "merge"} and self.replacement is not None:
            raise ValueError(f"{self.action} must not include replacement")
        if self.action == "merge" and len(self.source_memory_ids) < 2:
            raise ValueError("merge requires at least two source memories")
        if self.action != "keep" and not self.evidence_summary_ids:
            raise ValueError("non-keep action requires evidence_summary_ids")
        return self


class MemoryMaintenanceBatch(BaseModel):
    proposals: list[MemoryMaintenanceProposal] = Field(default_factory=list)


@dataclass
class MemoryMaintenanceApplyResult:
    proposal: MemoryMaintenanceProposal
    replacement_id: str | None = None
    changed_ids: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


class MemoryMaintainer:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def review(
        self,
        *,
        active_memories: list[MemoryRecord],
        summaries: list[SceneSummary],
        core_memory: CoreMemory,
        trace_id: str | None = None,
    ) -> MemoryMaintenanceBatch:
        if not active_memories or not summaries:
            return MemoryMaintenanceBatch()
        return self._llm.run(
            task="memory_maintain",
            input_data={
                "core_memory_json": core_memory.model_dump_json(indent=2),
                "active_memories_json": json.dumps(
                    [memory.model_dump(mode="json") for memory in active_memories],
                    ensure_ascii=False,
                    indent=2,
                ),
                "l1_summaries_json": json.dumps(
                    [summary.model_dump(mode="json") for summary in summaries],
                    ensure_ascii=False,
                    indent=2,
                ),
                "output_schema_json": json.dumps(
                    MemoryMaintenanceBatch.model_json_schema(),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            output_schema=MemoryMaintenanceBatch,
            trace_id=trace_id,
        )

    def apply(
        self,
        store: MemoryStore,
        proposal: MemoryMaintenanceProposal,
        active_memories: list[MemoryRecord],
        summaries: list[SceneSummary],
    ) -> MemoryMaintenanceApplyResult:
        result = MemoryMaintenanceApplyResult(proposal=proposal)
        validation_error = self.validate(
            proposal,
            active_memories=active_memories,
            summaries=summaries,
        )
        if validation_error:
            result.skipped_reason = validation_error
            return result

        active_by_id = {memory.id: memory for memory in active_memories}
        selected = [
            active_by_id[memory_id] for memory_id in proposal.source_memory_ids
        ]

        if proposal.action == "keep":
            return result

        if proposal.action in {"supersede", "merge"}:
            replacement = _build_replacement(proposal.replacement, selected)
            replacement_id = store.save_one_returning_id(replacement)
            changed_ids = [
                memory.id for memory in selected if memory.id != replacement_id
            ]
            store.set_status(
                changed_ids,
                status="superseded",
                reason=proposal.reason,
                superseded_by=replacement_id,
            )
            result.replacement_id = replacement_id
            result.changed_ids = changed_ids
            return result

        store.set_status(
            [memory.id for memory in selected],
            status="invalidated",
            reason=proposal.reason,
        )
        result.changed_ids = [memory.id for memory in selected]
        return result

    def validate(
        self,
        proposal: MemoryMaintenanceProposal,
        *,
        active_memories: list[MemoryRecord],
        summaries: list[SceneSummary],
    ) -> str | None:
        if len(proposal.source_memory_ids) != len(set(proposal.source_memory_ids)):
            return "duplicate_source_memory_id"
        active_ids = {memory.id for memory in active_memories}
        if any(memory_id not in active_ids for memory_id in proposal.source_memory_ids):
            return "source_memory_not_active_or_unknown"
        summary_ids = {summary.id for summary in summaries}
        if any(
            summary_id not in summary_ids
            for summary_id in proposal.evidence_summary_ids
        ):
            return "unknown_evidence_summary_id"
        if proposal.action != "keep" and not proposal.evidence_summary_ids:
            return "non_keep_proposal_requires_evidence"
        return None


def _build_replacement(
    replacement: ReplacementMemory,
    sources: list[MemoryRecord],
) -> Memory:
    return Memory(
        **replacement.model_dump(),
        source_scene_id=next(
            (source.source_scene_id for source in sources if source.source_scene_id),
            None,
        ),
        source_text_span=_unique(
            [span for source in sources for span in source.source_text_span]
        ),
        source_pending_ids=_unique(
            [pending_id for source in sources for pending_id in source.source_pending_ids]
        ),
    )


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
