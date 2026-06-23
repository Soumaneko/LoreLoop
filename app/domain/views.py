import json
from typing import Any

from app.domain.action.schemas import ActionParseResult
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.scene_summary.schemas import SceneSummary


def strip_empty(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: strip_empty(v)
            for k, v in obj.items()
            if v is not None and v != []
        }
    if isinstance(obj, list):
        return [strip_empty(item) for item in obj]
    return obj


MEMORY_CANDIDATE_EXCLUDE: set[str] = {
    "entities", "tags", "source_scene_id", "source_pending_ids",
    "score", "score_breakdown", "matched_anchors", "matched_actions",
}


def project_current_state(state: CurrentState) -> str:
    data = state.model_dump(mode="json")
    for npc in data.get("npcs", []):
        npc.pop("absent_turns", None)
    return json.dumps(
        strip_empty(data),
        ensure_ascii=False,
        indent=2,
    )


def project_entity_profiles(profiles: list[EntityProfileUpdateInput]) -> str:
    return json.dumps(
        [strip_empty(p.model_dump(mode="json")) for p in profiles],
        ensure_ascii=False,
        indent=2,
    )


def project_memory_candidates(candidates: list[MemoryCandidate]) -> str:
    return json.dumps(
        [c.model_dump(mode="json", exclude=MEMORY_CANDIDATE_EXCLUDE) for c in candidates],
        ensure_ascii=False,
        indent=2,
    )


def project_scene_summaries(summaries: list[SceneSummary]) -> str:
    exclude = {"id", "scene_ids", "source_ids", "created_at", "active", "condensed_into"}
    return json.dumps(
        [s.model_dump(mode="json", exclude=exclude) for s in summaries],
        ensure_ascii=False,
        indent=2,
    )


def project_action_parse(result: ActionParseResult) -> str:
    actions = []
    for a in result.atomic_actions:
        d = a.model_dump(mode="json", exclude={"likely_needs_roll", "confidence"})
        if d.get("condition") is None:
            d.pop("condition", None)
        if d.get("roll_hint") is None:
            d.pop("roll_hint", None)
        actions.append(d)
    data = {
        "turn_intent": result.turn_intent,
        "atomic_actions": actions,
        "ambiguities": result.ambiguities,
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def project_retrieved_memories(result: CandidateRetrievalResult) -> str:
    pending_exclude = {"score", "matched_anchors", "matched_actions"}
    memories = []

    for index, candidate in enumerate(result.candidates):
        data = candidate.model_dump(mode="json", exclude=MEMORY_CANDIDATE_EXCLUDE)
        data["memory_index"] = index
        data["source"] = "classified"
        memories.append(strip_empty(data))

    offset = len(memories)
    for pending_offset, note in enumerate(result.pending_notes):
        data = note.model_dump(mode="json", exclude=pending_exclude)
        data["memory_index"] = offset + pending_offset
        data["source"] = "pending"
        memories.append(strip_empty(data))

    data = {
        "memories": memories,
        "total_scanned": result.total_scanned,
        "total_returned": len(memories),
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
