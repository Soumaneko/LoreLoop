from dataclasses import dataclass, field

from app.core.llm import LLMGateway
from app.domain.current_state.schemas import CurrentState
from app.domain.recall_memory.batch_classifier import (
    MemoryBatchClassifier,
    MemoryBatchClassifierResult,
)
from app.domain.recall_memory.draft_extractor import MemoryDraftExtractor
from app.domain.recall_memory.extractor import MemoryExtractor
from app.domain.recall_memory.pending_store import PendingMemoryStore
from app.domain.recall_memory.schemas import Memory, MemoryDraftExtractResult
from app.domain.recall_memory.schemas import PendingMemoryItem
from app.domain.recall_memory.self_check import MemorySelfCheck, SelfCheckResult
from app.domain.recall_memory.llm_check import LLMCheck, LLMCheckResult
from app.domain.recall_memory.store import MemoryStore


@dataclass
class ExtractMemoryWorkflowResult:
    raw_memories: list[Memory] = field(default_factory=list)
    self_check_result: SelfCheckResult | None = None
    llm_check_result: LLMCheckResult | None = None
    final_memories: list[Memory] = field(default_factory=list)
    saved_ids: list[str] = field(default_factory=list)
    store_count_before: int = 0
    store_count_after: int = 0
    store_total: int = 0


@dataclass
class ExtractMemoryDraftsWorkflowResult:
    pending_items: list = field(default_factory=list)
    saved_pending_ids: list[str] = field(default_factory=list)
    pending_count_after: int = 0


@dataclass
class BatchClassifyPendingMemoryWorkflowResult:
    triggered: bool = False
    pending_count: int = 0
    unclassified_turn_count: int = 0
    raw_candidates: list = field(default_factory=list)
    final_memories: list[Memory] = field(default_factory=list)
    discarded_count: int = 0
    self_check_result: SelfCheckResult | None = None
    saved_ids: list[str] = field(default_factory=list)
    classified_pending_ids: list[str] = field(default_factory=list)
    batch_classify_count: int = 0
    should_consolidate: bool = False


def fill_pending_memory_scene_metadata(
    items: list[PendingMemoryItem],
    current_state: CurrentState,
) -> list[PendingMemoryItem]:
    updates = {
        "location": current_state.scene.location,
        "time_label": current_state.world_clock.label,
    }
    return [item.model_copy(update=updates) for item in items]


def run_extract_memory_workflow(
    scene_text: str,
    current_state: CurrentState,
    gateway: LLMGateway,
    db_path: str = "data/memories.db",
) -> ExtractMemoryWorkflowResult:
    extractor = MemoryExtractor(gateway, enable_self_check=False)

    raw = extractor.extract(scene_text, current_state)
    wfr = ExtractMemoryWorkflowResult(raw_memories=raw.memories)

    sc = MemorySelfCheck()
    cleaned, sc_result = sc.check(raw.memories, scene_text, current_state)
    wfr.self_check_result = sc_result

    if cleaned:
        llm_c = LLMCheck(gateway)
        cleaned, llm_result = llm_c.check(
            cleaned, scene_text, current_state,
            self_check_warnings=sc_result.warnings,
        )
        wfr.llm_check_result = llm_result

    wfr.final_memories = cleaned

    with MemoryStore(db_path) as store:
        wfr.store_count_before = store.count()
        wfr.saved_ids = store.save_many(cleaned)
        wfr.store_count_after = store.count()
        wfr.store_total = len(store.list_all())

    return wfr


def extract_memory_drafts_workflow(
    *,
    scene_text: str,
    current_state: CurrentState,
    turn_index: int,
    extractor: MemoryDraftExtractor,
    pending_store: PendingMemoryStore | None = None,
    scene_id: str | None = None,
    trace_id: str | None = None,
) -> ExtractMemoryDraftsWorkflowResult:
    result: MemoryDraftExtractResult = extractor.extract(
        scene_text,
        current_state,
        turn_index=turn_index,
        scene_id=scene_id,
        trace_id=trace_id,
    )
    items = fill_pending_memory_scene_metadata(result.items, current_state)
    wfr = ExtractMemoryDraftsWorkflowResult(pending_items=items)
    if pending_store is not None:
        wfr.saved_pending_ids = pending_store.save_many(items)
        wfr.pending_count_after = pending_store.count()
    return wfr


def batch_classify_pending_memories_workflow(
    *,
    current_state: CurrentState,
    classifier: MemoryBatchClassifier,
    pending_store: PendingMemoryStore,
    memory_store: MemoryStore,
    turn_threshold: int = 5,
    force: bool = False,
    trace_id: str | None = None,
) -> BatchClassifyPendingMemoryWorkflowResult:
    pending_items = pending_store.list_unclassified()
    turn_count = pending_store.unclassified_turn_count()
    wfr = BatchClassifyPendingMemoryWorkflowResult(
        pending_count=len(pending_items),
        unclassified_turn_count=turn_count,
    )

    if not pending_items:
        return wfr
    if not force and turn_count < turn_threshold:
        return wfr

    wfr.triggered = True
    result: MemoryBatchClassifierResult = classifier.classify(
        pending_items,
        current_state,
        trace_id=trace_id,
    )
    wfr.raw_candidates = result.raw_candidates
    wfr.final_memories = result.kept_memories
    wfr.discarded_count = len(result.discarded_candidates)
    wfr.self_check_result = result.self_check_result

    if result.kept_memories:
        wfr.saved_ids = memory_store.save_many(result.kept_memories)

    classified_ids = set()
    discarded_ids: list[str] = []
    for candidate in result.raw_candidates:
        classified_ids.update(candidate.source_pending_ids)
        if not candidate.keep:
            discarded_ids.extend(candidate.source_pending_ids)

    valid_pending_ids = {item.id for item in pending_items}
    classified_ids = valid_pending_ids

    if discarded_ids:
        pending_store.mark_discarded(
            [item_id for item_id in discarded_ids if item_id in valid_pending_ids],
            reason="discarded_by_batch_classifier",
        )

    pending_store.mark_classified(sorted(classified_ids))
    wfr.classified_pending_ids = sorted(classified_ids)
    wfr.batch_classify_count = pending_store.record_batch_classified()
    wfr.should_consolidate = pending_store.should_trigger_consolidation(every=3)
    return wfr


def extract_memory_workflow(
    scene_text: str,
    current_state: CurrentState,
    extractor: MemoryExtractor,
    store: MemoryStore,
) -> list[str]:
    result = extractor.extract(scene_text, current_state)
    ids = store.save_many(result.memories)
    return ids
