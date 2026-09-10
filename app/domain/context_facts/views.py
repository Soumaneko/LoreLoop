import json
from typing import Any

from app.domain.continuity import ContinuitySelection
from app.domain.context_facts.schemas import (
    ContextFactSet,
    ContextHistoryEvidenceSet,
    ContinuityInput,
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
    *,
    continuity_evidence_ids: set[str] | None = None,
) -> str:
    return json.dumps(
        project_context_history_payload(
            evidence,
            continuity_evidence_ids=continuity_evidence_ids,
        ),
        ensure_ascii=False,
        indent=2,
    )


def _history_reference(
    evidence_id: str,
    text: str,
    dedupe: set[str],
) -> dict[str, object]:
    if evidence_id in dedupe:
        return {"evidence_id": evidence_id, "text_in_continuity": True}
    return {"evidence_id": evidence_id, "text": text}


def project_context_history_payload(
    evidence: ContextHistoryEvidenceSet,
    *,
    continuity_evidence_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    """Render History hits, omitting text already carried by Continuity.

    ``continuity_evidence_ids`` lists paragraphs whose full prose is present in
    the Active Continuity ``source_evidence`` section. Those entries keep their
    ``evidence_id`` (still a valid citation target) but drop the duplicate
    ``text``; every other part of the hit, including non-duplicate adjacent and
    supporting paragraphs, is preserved. Selection, scoring, and budget are
    untouched: this is a final-payload de-duplication only.
    """
    dedupe = continuity_evidence_ids or set()
    records: list[dict[str, object]] = []
    for hit in evidence.hits:
        record: dict[str, object] = {
            "evidence_id": hit.evidence_id,
            "source_type": hit.source_type,
            "turn_index": hit.turn_index,
        }
        if hit.evidence_id in dedupe:
            record["text_in_continuity"] = True
        else:
            record["text"] = hit.text
            if hit.text_truncated:
                record["text_truncated"] = True
                record["original_text_chars"] = hit.original_text_chars
        if hit.context_before:
            record["context_before"] = [
                _history_reference(item.evidence_id, item.text, dedupe)
                for item in hit.context_before
            ]
        if hit.context_after:
            record["context_after"] = [
                _history_reference(item.evidence_id, item.text, dedupe)
                for item in hit.context_after
            ]
        if hit.matched_entities:
            record["matched_entities"] = hit.matched_entities
        if hit.matched_topics:
            record["matched_topics"] = hit.matched_topics
        if hit.supporting_evidence:
            record["supporting_evidence"] = [
                _history_reference(item.evidence_id, item.text, dedupe)
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


def project_continuity_input(continuity: ContinuityInput) -> str:
    return json.dumps(
        project_continuity_input_payload(continuity),
        ensure_ascii=False,
        indent=2,
    )


def project_continuity_input_payload(
    continuity: ContinuityInput,
) -> dict[str, Any]:
    """Render continuity items with update-bound prose, not ``current_state``.

    ``source_evidence`` is de-duplicated across items; each item points at the
    paragraphs that formed its latest update through ``source_evidence_ids``.
    An empty reference list is explicit: the item has no update-bound state.
    """
    items: list[dict[str, object]] = []
    for item in continuity.items:
        record: dict[str, object] = {
            "id": item.id,
            "kind": item.kind,
            "subject": item.subject,
            "premise": item.premise,
            "source_evidence_ids": list(item.source_evidence_ids),
        }
        if item.entities:
            record["entities"] = list(item.entities)
        if item.location:
            record["location"] = item.location
        if item.deadline:
            record["deadline"] = item.deadline
        items.append(record)
    return {
        "source_evidence": [
            {"evidence_id": paragraph.evidence_id, "text": paragraph.text}
            for paragraph in continuity.source_evidence
        ],
        "items": items,
    }


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
