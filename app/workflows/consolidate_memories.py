from dataclasses import dataclass, field

from app.domain.recall_memory.consolidator import (
    ConsolidationProposal,
    MemoryConsolidationApplyResult,
    MemoryConsolidationGroup,
    MemoryConsolidator,
)
from app.domain.recall_memory.store import MemoryStore


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
