import json

from app.domain.continuity import ContinuitySelection
from app.domain.context_facts.schemas import (
    ContextFactSet,
    ContextHistoryEvidenceSet,
    SettingSelection,
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


def project_context_history(
    evidence: ContextHistoryEvidenceSet,
) -> str:
    records = [
        {
            "evidence_id": hit.evidence_id,
            "source_type": hit.source_type,
            "turn_index": hit.turn_index,
            "context_before": [
                {"evidence_id": item.evidence_id, "text": item.text}
                for item in hit.context_before
            ],
            "text": hit.text,
            "text_truncated": hit.text_truncated,
            "original_text_chars": hit.original_text_chars,
            "context_after": [
                {"evidence_id": item.evidence_id, "text": item.text}
                for item in hit.context_after
            ],
            "matched_intent_ids": hit.matched_intent_ids,
            "matched_entities": hit.matched_entities,
            "matched_topics": hit.matched_topics,
            "supporting_evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "text": item.text,
                }
                for item in hit.supporting_evidence
            ],
        }
        for hit in evidence.hits
    ]
    return json.dumps(records, ensure_ascii=False, indent=2)


def project_continuity(selection: ContinuitySelection) -> str:
    return json.dumps(
        [
            {
                "id": match.item.id,
                "kind": match.item.kind,
                "subject": match.item.subject,
                "statement": match.item.statement,
                "entities": match.item.entities,
                "location": match.item.location,
                "deadline": match.item.deadline,
            }
            for match in selection.items
        ],
        ensure_ascii=False,
        indent=2,
    )


def project_settings(selection: SettingSelection) -> str:
    return json.dumps(
        [
            {
                "setting_id": match.setting_id,
                "kind": match.kind,
                "name": match.name,
                "summary": match.summary,
                "public_facts": match.public_facts,
            }
            for match in selection.settings
        ],
        ensure_ascii=False,
        indent=2,
    )
