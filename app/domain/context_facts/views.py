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
    return json.dumps(
        project_context_history_payload(evidence),
        ensure_ascii=False,
        indent=2,
    )


def project_context_history_payload(
    evidence: ContextHistoryEvidenceSet,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for hit in evidence.hits:
        record: dict[str, object] = {
            "evidence_id": hit.evidence_id,
            "source_type": hit.source_type,
            "turn_index": hit.turn_index,
            "text": hit.text,
        }
        if hit.context_before:
            record["context_before"] = [
                {"evidence_id": item.evidence_id, "text": item.text}
                for item in hit.context_before
            ]
        if hit.text_truncated:
            record["text_truncated"] = True
            record["original_text_chars"] = hit.original_text_chars
        if hit.context_after:
            record["context_after"] = [
                {"evidence_id": item.evidence_id, "text": item.text}
                for item in hit.context_after
            ]
        if hit.matched_entities:
            record["matched_entities"] = hit.matched_entities
        if hit.matched_topics:
            record["matched_topics"] = hit.matched_topics
        if hit.supporting_evidence:
            record["supporting_evidence"] = [
                {
                    "evidence_id": item.evidence_id,
                    "text": item.text,
                }
                for item in hit.supporting_evidence
            ]
        records.append(record)
    return records


def project_continuity(selection: ContinuitySelection) -> str:
    return json.dumps(
        project_continuity_payload(selection),
        ensure_ascii=False,
        indent=2,
    )


def project_continuity_payload(
    selection: ContinuitySelection,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for match in selection.items:
        item = match.item
        record: dict[str, object] = {
            "id": item.id,
            "kind": item.kind,
            "subject": item.subject,
            "premise": item.premise,
        }
        if item.current_state:
            record["current_state"] = item.current_state
        if item.entities:
            record["entities"] = item.entities
        if item.location:
            record["location"] = item.location
        if item.deadline:
            record["deadline"] = item.deadline
        records.append(record)
    return records


def project_settings(selection: SettingSelection) -> str:
    return json.dumps(
        project_settings_payload(selection),
        ensure_ascii=False,
        indent=2,
    )


def project_settings_payload(
    selection: SettingSelection,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for match in selection.settings:
        record: dict[str, object] = {
            "setting_id": match.setting_id,
            "kind": match.kind,
            "name": match.name,
            "summary": match.summary,
        }
        if match.public_facts:
            record["public_facts"] = match.public_facts
        records.append(record)
    return records
