import json
from typing import cast

from pydantic import BaseModel, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import LLMOutputParseError, LLMOutputValidationError
from app.core.llm.schema import schema_json
from app.core.text import normalize_compact_key
from app.domain.scene_facts.schemas import (
    SceneFact, SceneFactDraft, SceneFactDraftSet, SceneFactDraftSetLoose,
    SceneFactExtractInput, SceneFactSet, SceneNpcEvidence, SceneNpcMention,
)
from app.domain.views import project_current_state


class SceneFactExtractor:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def extract(self, input_data: SceneFactExtractInput, *, trace_id: str | None = None) -> SceneFactSet:
        if not input_data.scene_text.strip():
            return SceneFactSet(scene_focus="空场景正文")
        if not input_data.scene_paragraphs:
            raise ValueError("non-empty scene_text requires scene_paragraphs")
        paragraph_ids = [item.document_id for item in input_data.scene_paragraphs]
        if len(paragraph_ids) != len(set(paragraph_ids)):
            raise ValueError("scene_paragraphs document_id values must be unique")
        allowed_paragraphs = set(paragraph_ids)
        known = {normalize_compact_key(item["name"]): item for item in input_data.used_npcs}
        valid_facts: list[tuple[int, SceneFactDraft]] = []
        evidence: dict[str, SceneNpcEvidence] = {}
        reasons: list[str] = []
        focus = ""

        def validate_components(output: BaseModel) -> None:
            nonlocal valid_facts, evidence, reasons, focus
            draft = cast(SceneFactDraftSetLoose, output)
            valid_facts, evidence, reasons = [], {}, []
            focus = (draft.scene_focus or "").strip()
            errors = []
            keys = {"scene_focus", "facts", "npc_mentions"}
            if not focus or set(draft.model_fields_set) != keys:
                reasons.append("structure")
                errors.append("expected scene_focus, facts and npc_mentions")
            if len(draft.facts) > 12:
                reasons.append("structure")
                errors.append("facts must have at most 12 items")
            for index, item in enumerate(draft.facts[:12]):
                try:
                    valid_facts.append((index, SceneFactDraft.model_validate(item)))
                except ValidationError as exc:
                    reasons.append("facts")
                    errors.append(f"fact[{index}]: {exc}")
            allowed_facts = {f"f{index+1}" for index, _ in valid_facts}
            for index, item in enumerate(draft.npc_mentions):
                try:
                    record = SceneNpcEvidence.model_validate(item)
                except ValidationError as exc:
                    reasons.append("npc_mentions")
                    errors.append(f"npc_mentions[{index}]: {exc}")
                    continue
                key = normalize_compact_key(record.primary_surface)
                if key not in known:
                    continue
                bad_facts = set(record.evidence_fact_ids) - allowed_facts
                bad_paragraphs = set(record.evidence_paragraph_ids) - allowed_paragraphs
                if bad_facts or bad_paragraphs:
                    reasons.append("npc_mentions")
                    errors.append(f"npc_mentions[{index}]: unknown evidence {sorted(bad_facts | bad_paragraphs)}")
                previous = evidence.get(key)
                evidence[key] = SceneNpcEvidence(
                    primary_surface=known[key]["name"],
                    evidence_fact_ids=list(dict.fromkeys([
                        *(previous.evidence_fact_ids if previous else []),
                        *(value for value in record.evidence_fact_ids if value in allowed_facts),
                    ])),
                    evidence_paragraph_ids=list(dict.fromkeys([
                        *(previous.evidence_paragraph_ids if previous else []),
                        *(value for value in record.evidence_paragraph_ids if value in allowed_paragraphs),
                    ])),
                )
            if errors:
                raise ValueError("; ".join(errors))

        try:
            self.llm.run(
                task="scene_fact_extract",
                input_data={
                    "core_memory_json": input_data.core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(input_data.current_state),
                    "used_npcs_json": json.dumps(input_data.used_npcs, ensure_ascii=False, indent=2),
                    "scene_text": input_data.scene_text,
                    "scene_paragraphs_json": json.dumps([
                        {"document_id": item.document_id, "text": item.text}
                        for item in input_data.scene_paragraphs
                    ], ensure_ascii=False, indent=2),
                    "output_schema_json": schema_json(SceneFactDraftSet),
                },
                output_schema=SceneFactDraftSetLoose,
                output_validator=validate_components,
                temperature=0.2,
                trace_id=trace_id,
            )
        except LLMOutputValidationError:
            pass  # Keep valid components from the final attempt.
        except LLMOutputParseError as exc:
            reasons.append("parse")
        if "facts" in reasons:
            valid_facts = []
        fact_ids = {f"f{index+1}" for index, _ in valid_facts}
        mentions = []
        for key, npc in known.items():
            record = evidence.get(key)
            mentions.append(SceneNpcMention(
                primary_surface=npc["name"], presence=npc["presence"],
                surfaces=list(npc.get("surfaces", [])),
                evidence_fact_ids=[value for value in (record.evidence_fact_ids if record else []) if value in fact_ids],
                evidence_paragraph_ids=record.evidence_paragraph_ids if record else [],
            ))
        return SceneFactSet(
            scene_focus=focus or "无可用场景事实",
            facts=[SceneFact(fact_id=f"f{index+1}", fact=item.fact) for index, item in valid_facts],
            npc_mentions=mentions,
            degraded=bool(reasons), degraded_reasons=list(dict.fromkeys(reasons)),
        )
