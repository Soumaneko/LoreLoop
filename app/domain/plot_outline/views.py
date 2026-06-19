import json

from app.domain.narrative_pacer.schemas import NarrativePacingResult
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.views import MEMORY_CANDIDATE_EXCLUDE, strip_empty


def project_narrative_pacing(result: NarrativePacingResult) -> str:
    return json.dumps(
        result.model_dump(
            mode="json",
            exclude={"available_types", "previous_types", "roll_filter_notes", "cooldown_filter_notes"},
        ),
        ensure_ascii=False,
        indent=2,
    )


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
