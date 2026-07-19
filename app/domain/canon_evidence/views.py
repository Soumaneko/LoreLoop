import json

from app.domain.narrative_canon_query.schemas import NarrativeCanonQuerySet

from .schemas import CanonEvidenceSet


def project_canon_evidence_for_check(
    query_set: NarrativeCanonQuerySet | None,
    evidence_set: CanonEvidenceSet | None,
) -> str:
    if query_set is None or evidence_set is None:
        return "[]"

    evidence_by_query = {
        result.query_id: result.evidence
        for result in evidence_set.results
        if result.evidence
    }
    projected = []
    for query in query_set.queries:
        evidence = evidence_by_query.get(query.query_id, [])
        if not evidence:
            continue
        projected.append({
            "query_id": query.query_id,
            "paragraph_ids": list(query.paragraph_ids),
            "claim_type": query.claim_type,
            "audit_target": query.claim,
            "historical_records": [
                {
                    "evidence_id": item.evidence_id,
                    "source_type": item.source_type,
                    "turn_index": item.turn_index,
                    "context_before": item.context_before,
                    "text": item.text,
                    "context_after": item.context_after,
                }
                for item in evidence
            ],
        })
    return json.dumps(projected, ensure_ascii=False, indent=2)
