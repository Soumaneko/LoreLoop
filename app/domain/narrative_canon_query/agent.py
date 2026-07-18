import json
import re
from typing import cast

from app.core.llm import LLMGateway
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView

from .schemas import (
    CanonEntityReference,
    NarrativeCanonQueryDraftSet,
    NarrativeCanonQuerySet,
)


class NarrativeCanonQueryAgent:
    """Extract canon lookup requests from draft prose without retrieving or judging them."""

    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        *,
        scene_text: str,
        player_name: str,
        entity_profiles: list[EntityProfileUpdateInput],
        npc_profiles: list[NPCProfileDirectorView],
        trace_id: str | None = None,
    ) -> NarrativeCanonQuerySet:
        paragraphs = _split_paragraphs(scene_text)
        if not paragraphs:
            raise ValueError("narrative canon query requires non-empty scene prose")

        catalog = build_entity_catalog(
            player_name=player_name,
            entity_profiles=entity_profiles,
            npc_profiles=npc_profiles,
        )
        schema = NarrativeCanonQueryDraftSet.model_json_schema()
        result = cast(
            NarrativeCanonQueryDraftSet,
            self.llm.run(
                task="narrative_canon_query",
                input_data={
                    "numbered_scene_text": _number_paragraphs(paragraphs),
                    "known_entity_catalog_json": json.dumps(
                        [item.model_dump(mode="json") for item in catalog],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        schema,
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=NarrativeCanonQueryDraftSet,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
        return NarrativeCanonQuerySet.from_drafts(
            thought_process=result.thought_process,
            drafts=result.queries,
        )


def build_entity_catalog(
    *,
    player_name: str,
    entity_profiles: list[EntityProfileUpdateInput],
    npc_profiles: list[NPCProfileDirectorView],
) -> list[CanonEntityReference]:
    references = [
        CanonEntityReference(name=player_name.strip(), kind="player")
    ]
    references.extend(
        CanonEntityReference(
            name=profile.name.strip(),
            aliases=_clean_aliases(profile.aliases, profile.name),
            kind=profile.entity_type,
        )
        for profile in entity_profiles
        if profile.name.strip()
    )
    references.extend(
        CanonEntityReference(
            name=profile.name.strip(),
            aliases=_clean_aliases(profile.aliases, profile.name),
            kind="npc",
        )
        for profile in npc_profiles
        if profile.name.strip()
    )

    deduplicated: list[CanonEntityReference] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        key = (reference.kind, reference.name.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(reference)
    return deduplicated


def _clean_aliases(aliases: list[str], name: str) -> list[str]:
    clean: list[str] = []
    seen = {name.strip().casefold()}
    for alias in aliases:
        value = alias.strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        clean.append(value)
    return clean


def _split_paragraphs(scene_text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", scene_text.strip())]
    return [paragraph for paragraph in paragraphs if paragraph]


def _number_paragraphs(paragraphs: list[str]) -> str:
    return "\n\n".join(
        f"[P{index:03d}]\n{paragraph}"
        for index, paragraph in enumerate(paragraphs, start=1)
    )
