from app.core.llm import LLMGateway
from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    HistoryDocument,
    history_document_text_hash,
)
from app.domain.continuity import (
    ContinuityRetriever,
    ContinuitySelection,
    ContinuityStore,
)
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.world_setting import WorldSettingStore

from .extractor import ContextExtractor
from .continuity_bridge import ContinuityHistoryBridge
from .intent_builder import build_context_search_plan
from .retriever import ContextHistoryRetriever
from .setting_retriever import SettingRetriever
from .schemas import (
    ContextExtractorInput,
    ContextFactsInput,
    ContextFactsResult,
    ContinuityInput,
    ContinuityInputItem,
    ContinuityInputWarning,
    ContinuitySourceParagraph,
)
from app.domain.npc_profile.history_bridge import NPCProfileHistoryBridge


class ContextFactsService:
    def __init__(
        self,
        llm: LLMGateway,
        history_store: ConfirmedHistoryStore,
        continuity_store: ContinuityStore,
        setting_store: WorldSettingStore,
        npc_profile_store: NPCProfileStore,
    ):
        self.extractor = ContextExtractor(llm)
        self.history_store = history_store
        self.continuity_store = continuity_store
        self.setting_store = setting_store
        self.npc_profile_store = npc_profile_store

    def build(
        self,
        input_data: ContextFactsInput,
        *,
        trace_id: str | None = None,
    ) -> ContextFactsResult:
        search_plan = build_context_search_plan(
            user_input=input_data.user_input,
            current_state=input_data.current_state,
            matched_anchors=input_data.matched_anchors,
            npc_profiles=input_data.npc_profiles,
            retrieval_topics=input_data.retrieval_topics,
        )
        continuity_history_links = ContinuityHistoryBridge(
            self.continuity_store
        ).retrieve(search_plan)
        npc_history_links = NPCProfileHistoryBridge(
            self.npc_profile_store
        ).retrieve(search_plan)
        history_evidence = ContextHistoryRetriever(
            self.history_store
        ).retrieve(
            search_plan,
            current_turn_index=input_data.current_turn_index,
            continuity_links=continuity_history_links.links,
            npc_field_links=npc_history_links.links,
            matched_active_continuities=(
                continuity_history_links.matched_active_continuities
            ),
            matched_archived_continuities=(
                continuity_history_links.matched_archived_continuities
            ),
            matched_npc_profiles=len(npc_history_links.matched_profile_ids),
        )
        continuity = ContinuityRetriever(
            self.continuity_store
        ).retrieve(
            user_input=input_data.user_input,
            current_state=input_data.current_state,
            anchors=input_data.matched_anchors,
        )
        continuity_input, continuity_warnings = self._project_continuity_input(
            continuity
        )
        settings = SettingRetriever(
            self.setting_store
        ).retrieve(
            user_input=input_data.user_input,
            current_state=input_data.current_state,
            search_plan=search_plan,
        )
        context_facts = self.extractor.extract(
            ContextExtractorInput(
                user_input=input_data.user_input,
                core_memory=input_data.core_memory,
                current_state=input_data.current_state,
                scene_summaries=input_data.scene_summaries,
                history_evidence=history_evidence,
                continuity=continuity_input,
                settings=settings,
                npc_profiles=input_data.npc_profiles,
            ),
            trace_id=trace_id,
        )
        return ContextFactsResult(
            search_plan=search_plan,
            history_evidence=history_evidence,
            continuity=continuity,
            settings=settings,
            context_facts=context_facts,
            continuity_warnings=continuity_warnings,
        )

    def _project_continuity_input(
        self,
        selection: ContinuitySelection,
    ) -> tuple[ContinuityInput, list[ContinuityInputWarning]]:
        """Replace each item's ``current_state`` with its latest update prose.

        The item filtering (``ContinuityRetriever``) is unchanged. For every
        selected item the raw confirmed-history paragraphs bound to the most
        recent ``update`` are resolved by evidence ID and de-duplicated across
        the whole input, while each item keeps its ordered references. Items
        with no ``update`` log carry no state and no references.

        A reference must still be a live scene paragraph from the update's own
        turn whose text matches the hash recorded with the lifecycle log;
        ``documents_by_ids`` alone only proves the ID exists. Missing and stale
        references become explicit diagnostics, and the stale transcript is
        never used as a fallback.
        """
        items = [match.item for match in selection.items]
        if not items:
            return ContinuityInput(), []

        logs_by_item = {
            log.item_id: log
            for log in self.continuity_store.latest_update_logs(
                item.id for item in items
            )
        }

        candidate_ids: list[str] = []
        seen_candidates: set[str] = set()
        for item in items:
            log = logs_by_item.get(item.id)
            if log is None:
                continue
            for evidence in log.evidence:
                if evidence.evidence_id in seen_candidates:
                    continue
                seen_candidates.add(evidence.evidence_id)
                candidate_ids.append(evidence.evidence_id)

        documents = self.history_store.documents_by_ids(candidate_ids)

        valid_documents: dict[str, HistoryDocument] = {}
        warnings: list[ContinuityInputWarning] = []
        warned: set[tuple[str, str]] = set()

        def _warn(code: str, evidence_id: str, detail: str) -> None:
            if (code, evidence_id) in warned:
                return
            warned.add((code, evidence_id))
            warnings.append(ContinuityInputWarning(
                code=code,
                message=(
                    f"continuity update evidence {evidence_id} {detail}"
                ),
            ))

        input_items: list[ContinuityInputItem] = []
        for item in items:
            log = logs_by_item.get(item.id)
            evidence_ids: list[str] = []
            if log is not None:
                for evidence in log.evidence:
                    document = documents.get(evidence.evidence_id)
                    if document is None:
                        _warn(
                            "continuity_source_missing",
                            evidence.evidence_id,
                            "not found in confirmed history",
                        )
                        continue
                    if document.source_type != "scene_paragraph":
                        _warn(
                            "continuity_source_stale",
                            evidence.evidence_id,
                            "is not scene prose "
                            f"(source_type={document.source_type!r})",
                        )
                        continue
                    if document.turn_index != log.turn_index:
                        _warn(
                            "continuity_source_stale",
                            evidence.evidence_id,
                            "does not belong to the update turn "
                            f"(expected {log.turn_index}, "
                            f"got {document.turn_index})",
                        )
                        continue
                    if (
                        evidence.evidence_hash is not None
                        and history_document_text_hash(document)
                        != evidence.evidence_hash
                    ):
                        _warn(
                            "continuity_source_stale",
                            evidence.evidence_id,
                            "text no longer matches the recorded "
                            "evidence hash",
                        )
                        continue
                    evidence_ids.append(evidence.evidence_id)
                    valid_documents.setdefault(evidence.evidence_id, document)
            input_items.append(ContinuityInputItem(
                id=item.id,
                kind=item.kind,
                subject=item.subject,
                premise=item.premise,
                source_evidence_ids=evidence_ids,
                entities=list(item.entities),
                location=item.location,
                deadline=item.deadline,
            ))

        return (
            ContinuityInput(
                source_evidence=[
                    ContinuitySourceParagraph(
                        evidence_id=evidence_id,
                        text=valid_documents[evidence_id].text,
                    )
                    for evidence_id in candidate_ids
                    if evidence_id in valid_documents
                ],
                items=input_items,
            ),
            warnings,
        )
