import json

from app.domain.context_facts.schemas import (
    ContextFactSet,
    ContextHistoryEvidenceSet,
    ContextSearchPlan,
)


def project_context_facts(context_facts: ContextFactSet) -> str:
    data = [
        {
            "fact_id": fact.fact_id,
            "fact": fact.fact,
        }
        for fact in context_facts.facts
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)


def project_context_search_plan(search_plan: ContextSearchPlan) -> str:
    return search_plan.model_dump_json(indent=2)


def project_context_history(
    evidence: ContextHistoryEvidenceSet,
) -> str:
    records = [
        {
            "evidence_id": hit.evidence_id,
            "source_type": hit.source_type,
            "turn_index": hit.turn_index,
            "context_before": hit.context_before,
            "text": hit.text,
            "context_after": hit.context_after,
            "matched_intent_ids": hit.matched_intent_ids,
            "matched_entities": hit.matched_entities,
            "matched_topics": hit.matched_topics,
        }
        for hit in evidence.hits
    ]
    return json.dumps(records, ensure_ascii=False, indent=2)
