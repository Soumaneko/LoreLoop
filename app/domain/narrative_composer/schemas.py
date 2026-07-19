from pydantic import BaseModel, Field

from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEventConsumption
from app.domain.director_npc.schemas import DirectorNPCPlotInjection
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.plot_outline.schemas import PlannedEvent, PlotOutlineResult
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.domain.turn_resolution.schemas import TurnResolutionResult


class NarrativeRevisionRequest(BaseModel):
    previous_scene_text: str = Field(min_length=1)
    instruction: str = Field(min_length=1)


class NarrativeComposerContext(BaseModel):
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
    director_event_consumptions: list[DirectorEventConsumption] = Field(default_factory=list)
    director_npc_injection: DirectorNPCPlotInjection | None = None


class NarrativeComposerResult(BaseModel):
    director_event_reason: str | None = None
    director_npc_reason: str | None = None
    planning_reasoning: str = Field(min_length=1)
    must_establish_facts: list[str] = Field(default_factory=list)
    must_not_establish_facts: list[str] = Field(default_factory=list)
    information_boundaries: list[str] = Field(default_factory=list)
    scene_stopping_point: str = Field(min_length=1)
    planned_events: list[PlannedEvent] = Field(default_factory=list)

    scene_reasoning: str = Field(min_length=1)
    scene_text: str = Field(min_length=1)
    closing_prompt: str = Field(min_length=1)
    next_action_directions: list[NextActionDirection] = Field(
        default_factory=list,
        max_length=5,
    )

    def to_outline(self) -> PlotOutlineResult:
        return PlotOutlineResult.model_validate(
            self.model_dump(
                include={
                    "director_event_reason",
                    "director_npc_reason",
                    "planning_reasoning",
                    "must_establish_facts",
                    "must_not_establish_facts",
                    "information_boundaries",
                    "scene_stopping_point",
                    "planned_events",
                }
            )
        )

    def to_scene_result(self) -> SceneWriterResult:
        return SceneWriterResult.model_validate(
            self.model_dump(
                include={
                    "scene_reasoning",
                    "scene_text",
                    "closing_prompt",
                    "next_action_directions",
                }
            )
        )
