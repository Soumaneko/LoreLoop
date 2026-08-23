import json
from typing import cast

from pydantic import BaseModel, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import schema_json
from app.domain.scene_facts.schemas import (
    SceneFact,
    SceneFactDraft,
    SceneFactDraftSet,
    SceneFactDraftSetLoose,
    SceneFactExtractInput,
    SceneFactSet,
    SceneNpcMention,
)
from app.domain.views import project_current_state


class SceneFactExtractor:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def extract(
        self,
        input_data: SceneFactExtractInput,
        *,
        trace_id: str | None = None,
    ) -> SceneFactSet:
        if not input_data.scene_text.strip():
            return SceneFactSet(scene_focus="空场景正文", facts=[])

        if not input_data.scene_paragraphs:
            raise ValueError(
                "non-empty scene_text requires scene_paragraphs"
            )
        paragraph_ids = [
            paragraph.document_id for paragraph in input_data.scene_paragraphs
        ]
        if len(paragraph_ids) != len(set(paragraph_ids)):
            raise ValueError("scene_paragraphs document_id values must be unique")

        allowed_paragraph_ids = set(paragraph_ids)

        valid_fact_entries: list[tuple[int, SceneFactDraft]] = []
        valid_mentions: list[SceneNpcMention] = []
        facts_failed = False
        mentions_failed = False
        structure_failed = False
        last_focus = ""

        def validate_components(output: BaseModel) -> None:
            nonlocal valid_fact_entries, valid_mentions, facts_failed
            nonlocal mentions_failed, structure_failed, last_focus
            draft = cast(SceneFactDraftSetLoose, output)
            valid_fact_entries = []
            valid_mentions = []
            facts_failed = False
            mentions_failed = False
            structure_failed = False
            last_focus = (draft.scene_focus or "").strip()

            structure_errors: list[str] = []
            allowed_keys = {"scene_focus", "facts", "npc_mentions"}
            present = set(draft.model_fields_set)
            missing = sorted(allowed_keys - present)
            extra = sorted(set(draft.model_dump()) - allowed_keys)
            if missing:
                structure_errors.append(f"missing fields: {missing}")
            if extra:
                structure_errors.append(f"extra fields: {extra}")
            if not last_focus:
                structure_errors.append("scene_focus must be non-empty")
            if not isinstance(draft.facts, list):
                structure_errors.append("facts must be a list")
            elif len(draft.facts) > 12:
                structure_errors.append("facts must have at most 12 items")
            if not isinstance(draft.npc_mentions, list):
                structure_errors.append("npc_mentions must be a list")
            elif len(draft.npc_mentions) > 12:
                structure_errors.append(
                    "npc_mentions must have at most 12 items"
                )

            fact_errors: list[str] = []
            mention_errors: list[str] = []

            if isinstance(draft.facts, list):
                for index, item in enumerate(draft.facts[:12]):
                    try:
                        fact = SceneFactDraft.model_validate(item)
                    except ValidationError as exc:
                        fact_errors.append(f"fact[{index}]: {exc}")
                    else:
                        valid_fact_entries.append((index, fact))

            valid_fact_ids = {
                f"f{index + 1}" for index, _ in valid_fact_entries
            }

            if isinstance(draft.npc_mentions, list):
                for index, item in enumerate(draft.npc_mentions[:12]):
                    try:
                        mention = SceneNpcMention.model_validate(item)
                    except ValidationError as exc:
                        mention_errors.append(
                            f"npc_mentions[{index}]: {exc}"
                        )
                        continue

                    cleaned, clean_errors = _clean_mention_evidence(
                        mention,
                        valid_fact_ids=valid_fact_ids,
                        allowed_paragraph_ids=allowed_paragraph_ids,
                    )
                    if clean_errors:
                        mention_errors.extend(
                            f"npc_mentions[{index}]: {error}"
                            for error in clean_errors
                        )
                    if cleaned is not None:
                        valid_mentions.append(cleaned)

            facts_failed = bool(fact_errors)
            mentions_failed = bool(mention_errors)
            structure_failed = bool(structure_errors)

            if facts_failed or mentions_failed or structure_failed:
                feedback: list[str] = []
                if structure_errors:
                    feedback.append("structure: " + "; ".join(structure_errors))
                if fact_errors:
                    feedback.append("facts: " + "; ".join(fact_errors))
                if mention_errors:
                    feedback.append(
                        "npc_mentions: " + "; ".join(mention_errors)
                    )
                raise ValueError("; ".join(feedback))

        schema = schema_json(SceneFactDraftSet)
        staged_director_npcs_json = (
            json.dumps(input_data.staged_npcs, ensure_ascii=False, indent=2)
            if input_data.staged_npcs
            else "[]"
        )
        try:
            result = cast(
                SceneFactDraftSetLoose,
                self.llm.run(
                    task="scene_fact_extract",
                    input_data={
                        "core_memory_json": input_data.core_memory.model_dump_json(indent=2),
                        "current_state_json": project_current_state(input_data.current_state),
                        "staged_director_npcs_json": staged_director_npcs_json,
                        "scene_text": input_data.scene_text,
                        "scene_paragraphs_json": _scene_paragraphs_json(
                            input_data.scene_paragraphs
                        ),
                        "output_schema_json": schema,
                    },
                    output_schema=SceneFactDraftSetLoose,
                    output_validator=validate_components,
                    temperature=0.2,
                    trace_id=trace_id,
                ),
            )
            return SceneFactSet.from_drafts(
                scene_focus=result.scene_focus or "无可用场景事实",
                facts=[fact for _, fact in valid_fact_entries],
                npc_mentions=valid_mentions,
            )
        except LLMOutputValidationError:
            return _salvage_scene_fact_set(
                valid_fact_entries=valid_fact_entries,
                valid_mentions=valid_mentions,
                facts_failed=facts_failed,
                mentions_failed=mentions_failed,
                structure_failed=structure_failed,
                scene_focus=last_focus,
            )
        except LLMOutputParseError as exc:
            fallback_reason = "parse"
            return _salvage_scene_fact_set(
                valid_fact_entries=valid_fact_entries,
                valid_mentions=valid_mentions,
                facts_failed=facts_failed,
                mentions_failed=mentions_failed,
                structure_failed=structure_failed,
                scene_focus=last_focus,
                fallback_reason=fallback_reason,
            )
        except Exception:
            raise


def _clean_mention_evidence(
    mention: SceneNpcMention,
    *,
    valid_fact_ids: set[str],
    allowed_paragraph_ids: set[str],
) -> tuple[SceneNpcMention | None, list[str]]:
    errors: list[str] = []

    removed_facts = [
        fact_id
        for fact_id in mention.evidence_fact_ids
        if fact_id not in valid_fact_ids
    ]
    mention.evidence_fact_ids = [
        fact_id
        for fact_id in mention.evidence_fact_ids
        if fact_id in valid_fact_ids
    ]
    removed_paragraphs = [
        paragraph_id
        for paragraph_id in mention.evidence_paragraph_ids
        if paragraph_id not in allowed_paragraph_ids
    ]
    mention.evidence_paragraph_ids = [
        paragraph_id
        for paragraph_id in mention.evidence_paragraph_ids
        if paragraph_id in allowed_paragraph_ids
    ]
    if removed_facts:
        errors.append(f"removed unknown fact ids {sorted(removed_facts)}")
    if removed_paragraphs:
        errors.append(
            f"removed unknown paragraph ids {sorted(removed_paragraphs)}"
        )

    kept_relations = []
    for relation in mention.alias_relations:
        relation_removed_facts = [
            fact_id
            for fact_id in relation.evidence_fact_ids
            if fact_id not in valid_fact_ids
        ]
        relation.evidence_fact_ids = [
            fact_id
            for fact_id in relation.evidence_fact_ids
            if fact_id in valid_fact_ids
        ]
        relation_removed_paragraphs = [
            paragraph_id
            for paragraph_id in relation.evidence_paragraph_ids
            if paragraph_id not in allowed_paragraph_ids
        ]
        relation.evidence_paragraph_ids = [
            paragraph_id
            for paragraph_id in relation.evidence_paragraph_ids
            if paragraph_id in allowed_paragraph_ids
        ]
        if relation_removed_facts:
            errors.append(
                f"alias relation removed unknown fact ids "
                f"{sorted(relation_removed_facts)}"
            )
        if relation_removed_paragraphs:
            errors.append(
                f"alias relation removed unknown paragraph ids "
                f"{sorted(relation_removed_paragraphs)}"
            )
        if relation.basis == "explicit_fixed_alias" and not (
            relation.evidence_fact_ids or relation.evidence_paragraph_ids
        ):
            errors.append(
                "alias relation lost all evidence and was dropped"
            )
            continue
        kept_relations.append(relation)
    mention.alias_relations = kept_relations

    has_evidence = bool(
        mention.evidence_fact_ids
        or mention.evidence_paragraph_ids
        or any(
            relation.evidence_fact_ids or relation.evidence_paragraph_ids
            for relation in mention.alias_relations
        )
    )
    if not has_evidence:
        errors.append("no valid evidence after cleanup")
        return None, errors
    try:
        return SceneNpcMention.model_validate(mention.model_dump()), errors
    except ValidationError as exc:
        errors.append(str(exc))
        return None, errors


def _strip_all_fact_refs(mention: SceneNpcMention) -> SceneNpcMention | None:
    mention.evidence_fact_ids = []
    kept_relations = []
    for relation in mention.alias_relations:
        relation.evidence_fact_ids = []
        if relation.basis == "explicit_fixed_alias" and not (
            relation.evidence_paragraph_ids
        ):
            continue
        kept_relations.append(relation)
    mention.alias_relations = kept_relations
    has_evidence = bool(
        mention.evidence_paragraph_ids
        or any(
            relation.evidence_paragraph_ids
            for relation in mention.alias_relations
        )
    )
    if not has_evidence:
        return None
    try:
        return SceneNpcMention.model_validate(mention.model_dump())
    except ValidationError:
        return None


def _salvage_scene_fact_set(
    *,
    valid_fact_entries: list[tuple[int, SceneFactDraft]],
    valid_mentions: list[SceneNpcMention],
    facts_failed: bool,
    mentions_failed: bool,
    structure_failed: bool,
    scene_focus: str,
    fallback_reason: str | None = None,
) -> SceneFactSet:
    focus = scene_focus or "无可用场景事实"
    reasons: list[str] = []
    if facts_failed:
        reasons.append("facts")
    if mentions_failed:
        reasons.append("npc_mentions")
    if structure_failed:
        reasons.append("structure")
    if fallback_reason is not None:
        reasons.append(fallback_reason)

    if facts_failed:
        mentions = [
            mention
            for mention in (
                _strip_all_fact_refs(mention)
                for mention in valid_mentions
            )
            if mention is not None
        ]
        return SceneFactSet(
            scene_focus=focus,
            facts=[],
            npc_mentions=mentions,
            degraded=True,
            degraded_reasons=reasons,
        )

    facts = [
        SceneFact(
            fact_id=f"f{index + 1}",
            fact=fact.fact,
        )
        for index, fact in valid_fact_entries
    ]

    if not facts and not valid_mentions:
        return SceneFactSet(
            scene_focus=focus,
            facts=[],
            npc_mentions=[],
            degraded=True,
            degraded_reasons=reasons,
        )

    return SceneFactSet(
        scene_focus=focus,
        facts=facts,
        npc_mentions=valid_mentions,
        degraded=True,
        degraded_reasons=reasons,
    )


def _scene_paragraphs_json(paragraphs) -> str:
    return json.dumps(
        [
            {"document_id": paragraph.document_id, "text": paragraph.text}
            for paragraph in paragraphs
        ],
        ensure_ascii=False,
        indent=2,
    )
