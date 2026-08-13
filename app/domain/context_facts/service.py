from app.core.llm import LLMGateway
from app.domain.confirmed_history import ConfirmedHistoryStore
from app.domain.continuity import ContinuityRetriever, ContinuityStore
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
                continuity=continuity,
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
        )
