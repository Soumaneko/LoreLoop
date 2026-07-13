from pydantic import BaseModel, Field

from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCPlotInjection
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.turn_resolution.schemas import TurnResolutionResult


class PlotOutlineContext(BaseModel):
    user_input: str
    current_state: CurrentState
    turn_resolution: TurnResolutionResult
    core_memory: CoreMemory
    context_facts: ContextFactSet = Field(
        default_factory=lambda: ContextFactSet(
            turn_focus="No Context Facts",
            facts=[],
        )
    )
    entity_profiles: list[EntityProfileUpdateInput] = Field(default_factory=list)
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)
    director_events: list[DirectorEvent] = Field(default_factory=list)
    recent_resolved_director_event: DirectorEvent | None = None
    director_npc_injection: DirectorNPCPlotInjection | None = None


class PlannedEvent(BaseModel):
    event_id: str
    event_fact: str
    linked_outcome_ids: list[str] = Field(default_factory=list)
    involved_entities: list[str] = Field(default_factory=list)


class PlotOutlineResult(BaseModel):
    director_event_reason: str | None = None
    director_npc_reason: str | None = None
    planning_reasoning: str = Field(min_length=1)
    planned_events: list[PlannedEvent] = Field(default_factory=list)
    must_establish_facts: list[str] = Field(default_factory=list)
    must_not_establish_facts: list[str] = Field(default_factory=list)
    information_boundaries: list[str] = Field(default_factory=list)
    required_entities: list[str] = Field(default_factory=list)
    scene_stopping_point: str = Field(min_length=1)
