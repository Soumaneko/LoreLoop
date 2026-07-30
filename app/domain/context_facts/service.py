from app.core.llm import LLMGateway
from app.domain.confirmed_history import ConfirmedHistoryStore
from app.domain.continuity import ContinuityRetriever, ContinuityStore

from .extractor import ContextExtractor
from .intent_builder import build_context_search_plan
from .retriever import ContextHistoryRetriever
from .schemas import (
    ContextExtractorInput,
    ContextFactsInput,
    ContextFactsResult,
)


class ContextFactsService:
    def __init__(
        self,
        llm: LLMGateway,
        history_store: ConfirmedHistoryStore,
        continuity_store: ContinuityStore,
    ):
        self.extractor = ContextExtractor(llm)
        self.history_store = history_store
        self.continuity_store = continuity_store

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
            entity_profiles=input_data.entity_profiles,
            npc_profiles=input_data.npc_profiles,
        )
        history_evidence = ContextHistoryRetriever(
            self.history_store
        ).retrieve(
            search_plan,
            current_turn_index=input_data.current_turn_index,
        )
        continuity = ContinuityRetriever(
            self.continuity_store
        ).retrieve(
            user_input=input_data.user_input,
            current_state=input_data.current_state,
            anchors=input_data.matched_anchors,
        )
        context_facts = self.extractor.extract(
            ContextExtractorInput(
                user_input=input_data.user_input,
                core_memory=input_data.core_memory,
                current_state=input_data.current_state,
                scene_summaries=input_data.scene_summaries,
                search_plan=search_plan,
                history_evidence=history_evidence,
                continuity=continuity,
                entity_profiles=input_data.entity_profiles,
                npc_profiles=input_data.npc_profiles,
            ),
            trace_id=trace_id,
        )
        return ContextFactsResult(
            search_plan=search_plan,
            history_evidence=history_evidence,
            continuity=continuity,
            context_facts=context_facts,
        )
