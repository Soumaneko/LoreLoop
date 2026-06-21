from dataclasses import dataclass, field

from app.domain.current_state.schemas import CurrentState
from app.domain.entity.linker import EntityMemoryLinker
from app.domain.entity.store import EntityStore
from app.domain.recall_memory.batch_classifier import (
    MemoryBatchClassifier,
    MemoryBatchClassifierResult,
)
from app.domain.recall_memory.consolidator import (
    ConsolidationProposal,
    MemoryConsolidationApplyResult,
    MemoryConsolidationGroup,
    MemoryConsolidator,
)
from app.domain.recall_memory.draft_extractor import MemoryDraftExtractor
from app.domain.recall_memory.pending_store import PendingMemoryStore
from app.domain.recall_memory.schemas import Memory, MemoryDraftExtractResult, PendingMemoryItem
from app.domain.recall_memory.self_check import SelfCheckResult
from app.domain.recall_memory.store import MemoryStore


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
    entity_links_created: int = 0


@dataclass
class ConsolidateMemoriesWorkflowResult:
    active_count: int = 0
    groups: list[MemoryConsolidationGroup] = field(default_factory=list)
    proposals: list[ConsolidationProposal] = field(default_factory=list)
    apply_results: list[MemoryConsolidationApplyResult] = field(default_factory=list)

    @property
    def merged_count(self) -> int:
        return sum(1 for result in self.apply_results if result.merged_memory_id)

    @property
    def deactivated_count(self) -> int:
        return sum(len(result.deactivated_ids) for result in self.apply_results)


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
    entity_store: EntityStore | None = None,
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


def consolidate_memories_workflow(
    *,
    store: MemoryStore,
    consolidator: MemoryConsolidator,
    max_group_size: int = 5,
    trace_id: str | None = None,
) -> ConsolidateMemoriesWorkflowResult:
    active_memories = store.list_active()
    groups = consolidator.build_groups(
        active_memories,
        max_group_size=max_group_size,
    )
    result = ConsolidateMemoriesWorkflowResult(
        active_count=len(active_memories),
        groups=groups,
    )

    group_sources = {group.group_id: group.memories for group in groups}
    for group in groups:
        batch = consolidator.decide_group(group, trace_id=trace_id)
        result.proposals.extend(batch.proposals)
        for proposal in batch.proposals:
            sources = group_sources.get(proposal.group_id, group.memories)
            result.apply_results.append(
                consolidator.apply(store, proposal, sources)
            )

    return result
