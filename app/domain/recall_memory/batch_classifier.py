import json
from dataclasses import dataclass, field

from app.core.llm import LLMGateway
from app.domain.current_state.schemas import CurrentState
from app.domain.recall_memory.schemas import (
    ClassifiedMemoryCandidate,
    Memory,
    MemoryBatchClassificationResult,
    PendingMemoryItem,
)
from app.domain.recall_memory.self_check import MemorySelfCheck, SelfCheckResult


@dataclass
class MemoryBatchClassifierResult:
    raw_candidates: list[ClassifiedMemoryCandidate] = field(default_factory=list)
    kept_memories: list[Memory] = field(default_factory=list)
    discarded_candidates: list[ClassifiedMemoryCandidate] = field(default_factory=list)
    self_check_result: SelfCheckResult | None = None


class SimilarMemoryPreMerger:
    """Marker service for the LLM-side premerge step used during batch classification."""

    def describe(self) -> str:
        return "Batch classifier merges duplicate or highly similar pending items before save."


class MemoryBatchClassifier:
    def __init__(
        self,
        llm: LLMGateway,
        *,
        self_check: MemorySelfCheck | None = None,
        enable_self_check: bool = True,
    ):
        self._llm = llm
        self._self_check = self_check or MemorySelfCheck()
        self._enable_self_check = enable_self_check

    def classify(
        self,
        pending_items: list[PendingMemoryItem],
        current_state: CurrentState,
        *,
        trace_id: str | None = None,
    ) -> MemoryBatchClassifierResult:
        if not pending_items:
            return MemoryBatchClassifierResult()

        llm_result = self._llm.run(
            task="memory_batch_classify",
            input_data={
                "pending_items_json": json.dumps(
                    [item.model_dump(mode="json") for item in pending_items],
                    ensure_ascii=False,
                    indent=2,
                ),
                "current_state_summary_json": json.dumps(
                    _summarize_current_state(current_state),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            output_schema=MemoryBatchClassificationResult,
            trace_id=trace_id,
        )

        kept = [
            candidate.to_memory()
            for candidate in llm_result.candidates
            if candidate.keep
        ]
        discarded = [
            candidate
            for candidate in llm_result.candidates
            if not candidate.keep
        ]

        check_result = None
        if self._enable_self_check and kept:
            evidence_text = "\n".join(
                span
                for item in pending_items
                for span in item.source_text_span
            )
            kept, check_result = self._self_check.check(
                kept,
                evidence_text,
                current_state,
            )

        return MemoryBatchClassifierResult(
            raw_candidates=llm_result.candidates,
            kept_memories=kept,
            discarded_candidates=discarded,
            self_check_result=check_result,
        )


def _summarize_current_state(current_state: CurrentState) -> dict:
    return {
        "scene": current_state.scene.model_dump(mode="json"),
        "world_clock": current_state.world_clock.model_dump(mode="json"),
        "player": current_state.player.model_dump(mode="json"),
        "resources": current_state.resources.model_dump(mode="json"),
        "flags": getattr(current_state, "flags", []),
        "open_threads": getattr(current_state, "open_threads", []),
    }
