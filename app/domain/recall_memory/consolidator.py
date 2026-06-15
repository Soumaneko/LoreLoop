import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.llm import LLMGateway
from app.domain.recall_memory.schemas import (
    Memory,
    MemoryLayer,
    MemoryRecord,
    MemorySubtype,
)
from app.domain.recall_memory.store import MemoryStore


ConsolidationAction = Literal["keep_as_is", "merge"]


class ConsolidationProposal(BaseModel):
    group_id: str
    action: ConsolidationAction
    source_memory_ids: list[str] = Field(default_factory=list)

    layer: MemoryLayer | None = None
    subtype: MemorySubtype | None = None
    subject: str | None = None
    content: str | None = None
    location: str | None = None
    time_label: str | None = None
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def _validate_merge_fields(self):
        if self.action == "merge":
            missing = [
                name
                for name in ("layer", "subtype", "subject", "content")
                if getattr(self, name) in (None, "")
            ]
            if missing:
                raise ValueError(f"merge proposal missing fields: {', '.join(missing)}")
        return self


class MemoryConsolidationBatch(BaseModel):
    proposals: list[ConsolidationProposal] = Field(default_factory=list)


@dataclass
class MemoryConsolidationGroup:
    group_id: str
    memories: list[MemoryRecord] = field(default_factory=list)


@dataclass
class MemoryConsolidationApplyResult:
    proposal: ConsolidationProposal
    merged_memory_id: str | None = None
    deactivated_ids: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


GENERIC_ENTITIES = {
    "玩家",
    "主角",
    "你",
    "他",
    "她",
    "他们",
    "她们",
    "众人",
    "所有人",
}


class MemoryConsolidator:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def build_groups(
        self,
        memories: list[MemoryRecord],
        *,
        max_group_size: int = 5,
    ) -> list[MemoryConsolidationGroup]:
        if max_group_size < 2:
            raise ValueError("max_group_size must be >= 2")
        if len(memories) < 2:
            return []

        parent = {memory.id: memory.id for memory in memories}
        by_id = {memory.id: memory for memory in memories}

        def find(memory_id: str) -> str:
            while parent[memory_id] != memory_id:
                parent[memory_id] = parent[parent[memory_id]]
                memory_id = parent[memory_id]
            return memory_id

        def union(a: str, b: str) -> None:
            root_a = find(a)
            root_b = find(b)
            if root_a != root_b:
                parent[root_b] = root_a

        for index, left in enumerate(memories):
            for right in memories[index + 1:]:
                if _same_subject(left, right) or _entity_overlap(left, right) or _same_location_layer(left, right):
                    union(left.id, right.id)

        buckets: dict[str, list[MemoryRecord]] = {}
        for memory in memories:
            buckets.setdefault(find(memory.id), []).append(memory)

        groups: list[MemoryConsolidationGroup] = []
        group_index = 1
        for records in buckets.values():
            if len(records) < 2:
                continue
            records = sorted(records, key=lambda item: item.created_at, reverse=True)
            for start in range(0, len(records), max_group_size):
                chunk = records[start:start + max_group_size]
                if len(chunk) < 2:
                    continue
                groups.append(
                    MemoryConsolidationGroup(
                        group_id=f"group_{group_index}",
                        memories=chunk,
                    )
                )
                group_index += 1

        return groups

    def decide_group(
        self,
        group: MemoryConsolidationGroup,
        *,
        trace_id: str | None = None,
    ) -> MemoryConsolidationBatch:
        return self._llm.run(
            task="memory_consolidate",
            input_data={
                "group_id": group.group_id,
                "group_json": json.dumps(
                    [memory.model_dump(mode="json") for memory in group.memories],
                    ensure_ascii=False,
                    indent=2,
                ),
                "output_schema_json": json.dumps(
                    MemoryConsolidationBatch.model_json_schema(),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            output_schema=MemoryConsolidationBatch,
            trace_id=trace_id,
        )

    def apply(
        self,
        store: MemoryStore,
        proposal: ConsolidationProposal,
        source_memories: list[MemoryRecord],
    ) -> MemoryConsolidationApplyResult:
        result = MemoryConsolidationApplyResult(proposal=proposal)
        if proposal.action == "keep_as_is":
            return result
        if not proposal.source_memory_ids:
            result.skipped_reason = "missing_source_memory_ids"
            return result

        source_by_id = {memory.id: memory for memory in source_memories}
        selected = [
            source_by_id[memory_id]
            for memory_id in proposal.source_memory_ids
            if memory_id in source_by_id
        ]
        if len(selected) < 2:
            result.skipped_reason = "fewer_than_two_valid_sources"
            return result

        validation_reason = self._validate_proposal(proposal, selected)
        if validation_reason:
            result.skipped_reason = validation_reason
            return result

        merged = _build_memory_from_proposal(proposal, selected)
        merged_id = store.save_one_returning_id(merged)
        source_ids_to_deactivate = [
            memory.id
            for memory in selected
            if memory.id != merged_id
        ]
        store.deactivate_many(source_ids_to_deactivate, merged_into=merged_id)
        result.merged_memory_id = merged_id
        result.deactivated_ids = source_ids_to_deactivate
        return result

    @staticmethod
    def _validate_proposal(
        proposal: ConsolidationProposal,
        sources: list[MemoryRecord],
    ) -> str | None:
        content = (proposal.content or "").strip()
        if content:
            src_total = sum(len((s.content or "").strip()) for s in sources)
            if src_total > 0 and len(content) >= src_total * 1:
                return "content_is_简单拼接"

        locations = {s.location.strip() for s in sources if s.location and s.location.strip()}
        if len(locations) > 1:
            return "跨子场景"

        return None

        merged = _build_memory_from_proposal(proposal, selected)
        merged_id = store.save_one_returning_id(merged)
        source_ids_to_deactivate = [
            memory.id
            for memory in selected
            if memory.id != merged_id
        ]
        store.deactivate_many(source_ids_to_deactivate, merged_into=merged_id)
        result.merged_memory_id = merged_id
        result.deactivated_ids = source_ids_to_deactivate
        return result


def _same_subject(left: MemoryRecord, right: MemoryRecord) -> bool:
    return bool(left.subject and left.subject == right.subject)


def _entity_overlap(left: MemoryRecord, right: MemoryRecord) -> bool:
    left_entities = set(left.entities) - GENERIC_ENTITIES
    right_entities = set(right.entities) - GENERIC_ENTITIES
    return bool(left_entities and right_entities and left_entities & right_entities)


def _same_location_layer(left: MemoryRecord, right: MemoryRecord) -> bool:
    return bool(left.location and left.location == right.location and left.layer == right.layer)


def _unique(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _build_memory_from_proposal(
    proposal: ConsolidationProposal,
    sources: list[MemoryRecord],
) -> Memory:
    source_scene_id = next((source.source_scene_id for source in sources if source.source_scene_id), None)
    return Memory(
        layer=proposal.layer,
        subtype=proposal.subtype,
        subject=proposal.subject or sources[0].subject,
        content=proposal.content or sources[0].content,
        entities=_unique([entity for source in sources for entity in source.entities]),
        location=proposal.location if proposal.location is not None else sources[0].location,
        time_label=proposal.time_label if proposal.time_label is not None else sources[0].time_label,
        visibility=_unique([name for source in sources for name in source.visibility]),
        tags=_unique([*proposal.tags, *[tag for source in sources for tag in source.tags]]),
        confidence=max(source.confidence for source in sources),
        certainty=max(source.certainty for source in sources),
        salience=max(source.salience for source in sources),
        owner=proposal.owner,
        source_scene_id=source_scene_id,
        source_text_span=_unique([span for source in sources for span in source.source_text_span]),
        source_pending_ids=_unique([pid for source in sources for pid in source.source_pending_ids]),
    )
