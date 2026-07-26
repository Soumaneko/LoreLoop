from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.npc_profile.store import NPCProfileStore

from .extractor import SettingSeedExtractor
from .maintainer import SettingMaintainer
from .schemas import (
    SettingMaintenanceResult,
    SettingSeedCandidate,
    SettingSeedExtractionResult,
    SettingSourceDocument,
)
from .store import WorldSettingStore


@dataclass(frozen=True)
class SettingSeedExtractionWorkflowResult:
    extraction: SettingSeedExtractionResult
    saved_setting_ids: list[str]
    ignored_candidate_count: int


@dataclass(frozen=True)
class SettingMaintenanceWorkflowResult:
    maintenance: SettingMaintenanceResult
    retained_setting_ids: list[str]


def maintain_setting_seeds(
    *,
    core_memory: CoreMemory,
    turn_index: int,
    world_setting_store: WorldSettingStore,
    gateway: LLMGateway,
    trace_id: str | None = None,
) -> SettingMaintenanceWorkflowResult:
    if world_setting_store.last_maintained_turn() == turn_index:
        return SettingMaintenanceWorkflowResult(
            maintenance=SettingMaintenanceResult(),
            retained_setting_ids=[],
        )
    seeds = world_setting_store.list_maintenance_seeds()
    if not seeds:
        return SettingMaintenanceWorkflowResult(
            maintenance=SettingMaintenanceResult(),
            retained_setting_ids=[],
        )
    maintenance = SettingMaintainer(gateway).maintain(
        core_memory=core_memory,
        seeds=seeds,
        mature_settings=world_setting_store.list_maintenance_targets(),
        trace_id=trace_id,
    )
    retained_ids = world_setting_store.apply_maintenance(
        maintenance,
        turn_index=turn_index,
    )
    return SettingMaintenanceWorkflowResult(
        maintenance=maintenance,
        retained_setting_ids=retained_ids,
    )


def extract_setting_seeds(
    *,
    core_memory: CoreMemory,
    scene_text: str,
    scene_facts_json: str,
    turn_index: int,
    new_npc_profile_ids: list[str],
    world_setting_store: WorldSettingStore,
    npc_profile_store: NPCProfileStore,
    gateway: LLMGateway,
    trace_id: str | None = None,
) -> SettingSeedExtractionWorkflowResult:
    documents = _build_source_documents(
        core_memory=core_memory,
        scene_text=scene_text,
        turn_index=turn_index,
        new_npc_profile_ids=new_npc_profile_ids,
        npc_profile_store=npc_profile_store,
        include_core_memory=world_setting_store.count() == 0,
    )
    extraction = SettingSeedExtractor(gateway).extract(
        core_memory=core_memory,
        scene_facts_json=scene_facts_json,
        source_documents=documents,
        trace_id=trace_id,
    )
    documents_by_key = {
        document.source_key: document for document in documents
    }
    valid_candidates = [
        candidate
        for candidate in extraction.candidates
        if _candidate_source_is_valid(candidate, documents_by_key)
    ]
    saved_ids = world_setting_store.apply_extraction(
        valid_candidates,
        source_documents=documents_by_key,
        turn_index=turn_index,
    )
    return SettingSeedExtractionWorkflowResult(
        extraction=extraction,
        saved_setting_ids=saved_ids,
        ignored_candidate_count=len(extraction.candidates) - len(valid_candidates),
    )


def _build_source_documents(
    *,
    core_memory: CoreMemory,
    scene_text: str,
    turn_index: int,
    new_npc_profile_ids: list[str],
    npc_profile_store: NPCProfileStore,
    include_core_memory: bool,
) -> list[SettingSourceDocument]:
    documents = [
        SettingSourceDocument(
            source_key=f"scene:{turn_index}",
            source_type="scene_text",
            source_ref=f"turn:{turn_index}",
            visibility="player_known",
            content=scene_text,
        )
    ]
    if include_core_memory:
        documents.append(
            SettingSourceDocument(
                source_key="core_memory",
                source_type="core_memory",
                source_ref="core_memory",
                visibility="director_private",
                content=core_memory.model_dump_json(indent=2),
            )
        )
    for npc_id in dict.fromkeys(new_npc_profile_ids):
        profile = npc_profile_store.get_by_id(npc_id)
        if profile is None:
            continue
        documents.append(
            SettingSourceDocument(
                source_key=f"npc:{npc_id}:public",
                source_type="npc_profile_public",
                source_ref=npc_id,
                visibility="player_known",
                content=profile.public_view().model_dump_json(indent=2),
            )
        )
        private_content = {
            "name": profile.name,
            "core_motivation": profile.core_motivation,
            "hidden_secret": profile.hidden_secret,
            "conflict_style": profile.conflict_style,
            "social_style": profile.social_style,
            "encounter_potentials": profile.encounter_potentials,
            "director_notes": profile.director_notes,
        }
        if any(
            value
            for key, value in private_content.items()
            if key != "name"
        ):
            documents.append(
                SettingSourceDocument(
                    source_key=f"npc:{npc_id}:private",
                    source_type="npc_profile_private",
                    source_ref=npc_id,
                    visibility="director_private",
                    content=json.dumps(
                        private_content,
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            )
    return documents


def _candidate_source_is_valid(
    candidate: SettingSeedCandidate,
    documents: dict[str, SettingSourceDocument],
) -> bool:
    document = documents.get(candidate.source_key)
    if document is None:
        return False
    span = _normalize(candidate.source_span)
    return bool(span) and span in _normalize(document.content)


def _normalize(value: str) -> str:
    return "".join(str(value).casefold().split())
