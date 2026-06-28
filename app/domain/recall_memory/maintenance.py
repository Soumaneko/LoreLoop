from dataclasses import dataclass, field
from threading import Lock

from app.domain.current_state.schemas import CurrentState
from app.domain.core_memory.schemas import CoreMemory
from app.domain.entity.linker import EntityMemoryLinker
from app.domain.entity.store import EntityStore
from app.domain.recall_memory.batch_classifier import (
    MemoryBatchClassifier,
    MemoryBatchClassifierResult,
)
from app.domain.recall_memory.maintainer import (
    MemoryMaintenanceApplyResult,
    MemoryMaintenanceProposal,
    MemoryMaintainer,
)
from app.domain.recall_memory.draft_extractor import MemoryDraftExtractor
from app.domain.recall_memory.pending_store import PendingMemoryStore
from app.domain.recall_memory.schemas import Memory, MemoryDraftExtractResult, PendingMemoryItem
from app.domain.recall_memory.self_check import SelfCheckResult
from app.domain.recall_memory.store import MemoryStore
from app.domain.scene_summary.store import SceneSummaryStore

_MEMORY_BATCH_LOCK = Lock()


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
    should_maintain: bool = False
    entity_links_created: int = 0


@dataclass
class MaintainMemoriesWorkflowResult:
    active_count: int = 0
    summary_count: int = 0
    cursor_before: int = 0
    cursor_after: int = 0
    proposals: list[MemoryMaintenanceProposal] = field(default_factory=list)
    apply_results: list[MemoryMaintenanceApplyResult] = field(default_factory=list)

    @property
    def changed_count(self) -> int:
        return sum(len(result.changed_ids) for result in self.apply_results)


def fill_pending_memory_scene_metadata(
    items: list[PendingMemoryItem],
    current_state: CurrentState,
) -> list[PendingMemoryItem]:
    updates = {
        "location": current_state.scene.location,
        "time_label": current_state.world_clock.label,
    }
    return [item.model_copy(update=updates) for item in items]


def extract_memory_drafts_workflow(
    *,
    scene_text: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    turn_index: int,
    extractor: MemoryDraftExtractor,
    pending_store: PendingMemoryStore | None = None,
    scene_id: str | None = None,
    trace_id: str | None = None,
) -> ExtractMemoryDraftsWorkflowResult:
    result: MemoryDraftExtractResult = extractor.extract(
        scene_text,
        current_state,
        core_memory=core_memory,
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
    core_memory: CoreMemory,
    classifier: MemoryBatchClassifier,
    pending_store: PendingMemoryStore,
    memory_store: MemoryStore,
    entity_store: EntityStore | None = None,
    summary_store: SceneSummaryStore | None = None,
    turn_threshold: int = 5,
    force: bool = False,
    trace_id: str | None = None,
) -> BatchClassifyPendingMemoryWorkflowResult:
    with _MEMORY_BATCH_LOCK:
        return _batch_classify_pending_memories_locked(
            current_state=current_state,
            core_memory=core_memory,
            classifier=classifier,
            pending_store=pending_store,
            memory_store=memory_store,
            entity_store=entity_store,
            summary_store=summary_store,
            turn_threshold=turn_threshold,
            force=force,
            trace_id=trace_id,
        )


def _batch_classify_pending_memories_locked(
    *,
    current_state: CurrentState,
    core_memory: CoreMemory,
    classifier: MemoryBatchClassifier,
    pending_store: PendingMemoryStore,
    memory_store: MemoryStore,
    entity_store: EntityStore | None = None,
    summary_store: SceneSummaryStore | None = None,
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
    recent_summaries = (
        summary_store.get_recent(5) if summary_store is not None else []
    )
    result: MemoryBatchClassifierResult = classifier.classify(
        pending_items,
        current_state,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        trace_id=trace_id,
    )
    wfr.raw_candidates = result.raw_candidates
    wfr.final_memories = result.kept_memories
    wfr.discarded_count = len(result.discarded_candidates)
    wfr.self_check_result = result.self_check_result

    if result.kept_memories:
        wfr.saved_ids = memory_store.save_many(result.kept_memories)
        if entity_store is not None and wfr.saved_ids:
            records = [
                record
                for memory_id in wfr.saved_ids
                if (record := memory_store.get_by_id(memory_id)) is not None
            ]
            links = EntityMemoryLinker(entity_store).link_records(records)
            wfr.entity_links_created = len(links)

    classified_ids = set()
    discarded_ids: list[str] = []
    for candidate in result.raw_candidates:
        classified_ids.update(candidate.source_pending_ids)
        if not candidate.keep:
            discarded_ids.extend(candidate.source_pending_ids)

    valid_pending_ids = {item.id for item in pending_items}
    classified_ids &= valid_pending_ids

    if discarded_ids:
        pending_store.mark_discarded(
            [item_id for item_id in discarded_ids if item_id in valid_pending_ids],
            reason="discarded_by_batch_classifier",
        )

    pending_store.mark_classified(sorted(classified_ids))
    wfr.classified_pending_ids = sorted(classified_ids)
    wfr.batch_classify_count = pending_store.record_batch_classified()
    wfr.should_maintain = True
    return wfr


def maintain_memories_workflow(
    *,
    store: MemoryStore,
    summary_store: SceneSummaryStore,
    maintainer: MemoryMaintainer,
    core_memory: CoreMemory,
    through_turn: int | None = None,
    trace_id: str | None = None,
) -> MaintainMemoriesWorkflowResult:
    cursor = store.get_meta_int("memory_maintainer_last_turn", -1)
    summaries = summary_store.list_l1_since(
        cursor,
        through_turn=through_turn,
    )
    active_memories = store.list_active()
    result = MaintainMemoriesWorkflowResult(
        active_count=len(active_memories),
        summary_count=len(summaries),
        cursor_before=cursor,
        cursor_after=cursor,
    )
    if not summaries:
        return result

    if active_memories:
        batch = maintainer.review(
            active_memories=active_memories,
            summaries=summaries,
            core_memory=core_memory,
            trace_id=trace_id,
        )
        result.proposals = batch.proposals
        used_memory_ids: set[str] = set()
        validation_errors: list[str | None] = []
        for proposal in batch.proposals:
            duplicate_ids = used_memory_ids & set(proposal.source_memory_ids)
            if duplicate_ids:
                validation_errors.append(
                    "memory_referenced_by_multiple_proposals:"
                    + ",".join(sorted(duplicate_ids))
                )
            else:
                validation_errors.append(
                    maintainer.validate(
                        proposal,
                        active_memories=active_memories,
                        summaries=summaries,
                    )
                )
            used_memory_ids.update(proposal.source_memory_ids)

        if any(validation_errors):
            result.apply_results = [
                MemoryMaintenanceApplyResult(
                    proposal=proposal,
                    skipped_reason=error or "maintenance_batch_validation_failed",
                )
                for proposal, error in zip(batch.proposals, validation_errors)
            ]
            return result

        for proposal in batch.proposals:
            result.apply_results.append(
                maintainer.apply(
                    store,
                    proposal,
                    active_memories,
                    summaries,
                )
            )

    cursor_after = max(summary.turn_end for summary in summaries)
    store.set_meta_int("memory_maintainer_last_turn", cursor_after)
    result.cursor_after = cursor_after
    return result
