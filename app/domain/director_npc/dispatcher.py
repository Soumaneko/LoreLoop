import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState, NPCState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_event.views import project_director_event
from app.domain.director_npc.schemas import (
    DispatchedDirectorNPCCandidate,
    DirectorNPCDispatchDecision,
    DirectorNPCDraft,
    ExistingNPCCandidate,
)
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import project_current_state, project_scene_summaries


class DirectorNPCDispatcher:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def dispatch(
        self,
        *,
        core_memory: CoreMemory,
        current_state: CurrentState,
        recent_summaries: list[SceneSummary],
        director_event: DirectorEvent | None,
        standby_npcs: list[DirectorNPCDraft],
        existing_candidates: list[NPCState],
        dispatched_director_npc_candidates: list[DispatchedDirectorNPCCandidate],
        trigger_reason: str,
        trace_id: str | None = None,
    ) -> DirectorNPCDispatchDecision:
        result = cast(
            DirectorNPCDispatchDecision,
            self._llm.run(
                task="director_npc_dispatch",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(current_state),
                    "scene_summaries_json": project_scene_summaries(recent_summaries),
                    "director_event_json": project_director_event(director_event),
                    "standby_npcs_json": json.dumps(
                        [npc.model_dump(mode="json") for npc in standby_npcs],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "existing_npc_candidates_json": json.dumps(
                        [
                            ExistingNPCCandidate(
                                name=npc.name,
                                role=npc.role,
                                current_intent=npc.current_intent,
                                attitude_to_player=npc.attitude_to_player,
                                absent_turns=npc.absent_turns,
                            ).model_dump(mode="json")
                            for npc in existing_candidates
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "dispatched_director_npc_candidates_json": json.dumps(
                        [
                            candidate.model_dump(mode="json")
                            for candidate in dispatched_director_npc_candidates
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "trigger_reason": trigger_reason,
                    "output_schema_json": json.dumps(
                        DirectorNPCDispatchDecision.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=DirectorNPCDispatchDecision,
                temperature=0.8,
                trace_id=trace_id,
            ),
        )
        return result
