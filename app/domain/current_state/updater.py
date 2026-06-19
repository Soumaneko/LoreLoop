import json
import re

from app.core.llm import LLMGateway
from app.domain.current_state.schemas import (
    CurrentState,
    NPCState,
    PlayerState,
    ResourcesState,
)
from pydantic import BaseModel, Field


_STALE_NPC_THRESHOLD = 3


class _SceneStateUpdate(BaseModel):
    location: str
    participants: list[str] = Field(default_factory=list)
    atmosphere: str | None = None


class _WorldClockSuggestion(BaseModel):
    advance_phases: int = Field(default=0, ge=0, le=24)
    reason: str | None = None


class _NPCStateUpdate(BaseModel):
    name: str
    role: str | None = None
    location: str | None = None
    condition: list[str] = Field(default_factory=list)
    attitude_to_player: str | None = None
    current_intent: str | None = None


class _CurrentStateUpdate(BaseModel):
    scene: _SceneStateUpdate
    world_clock_suggestion: _WorldClockSuggestion = Field(default_factory=_WorldClockSuggestion)
    player: PlayerState
    npcs: list[_NPCStateUpdate] = Field(default_factory=list)
    resources: ResourcesState = Field(default_factory=ResourcesState)
    mentioned_npcs: list[str] = Field(default_factory=list)


def _increment_scene_id(scene_id: str) -> str:
    m = re.search(r"(\d+)$", scene_id)
    if m:
        num = int(m.group(1)) + 1
        width = len(m.group(1))
        return scene_id[: m.start()] + str(num).zfill(width)
    return scene_id + "_1"


class StateUpdater:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def update(
        self,
        current_state: CurrentState,
        scene_text: str,
        *,
        trace_id: str | None = None,
    ) -> CurrentState:
        state_for_llm = current_state.model_copy(deep=True)
        state_data = state_for_llm.model_dump()
        state_data["scene"].pop("scene_id", None)
        state_data.pop("world_clock", None)
        for npc in state_data.get("npcs", []):
            npc.pop("absent_turns", None)

        schema = _CurrentStateUpdate.model_json_schema()

        llm_result = self.llm.run(
            task="state_update",
            input_data={
                "current_state_json": json.dumps(state_data, ensure_ascii=False, indent=2),
                "scene_text": scene_text,
                "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
            },
            output_schema=_CurrentStateUpdate,
            trace_id=trace_id,
        )

        if llm_result.scene.location != current_state.scene.location:
            scene_id = _increment_scene_id(current_state.scene.scene_id)
        else:
            scene_id = current_state.scene.scene_id
        world_clock = current_state.world_clock.advance_phase(
            llm_result.world_clock_suggestion.advance_phases
        )
        npcs = _apply_absent_turns(
            current_npcs=current_state.npcs,
            updated_npcs=llm_result.npcs,
            mentioned_npcs=llm_result.mentioned_npcs,
        )

        return CurrentState(
            scene={
                **llm_result.scene.model_dump(),
                "scene_id": scene_id,
            },
            world_clock=world_clock,
            player=llm_result.player,
            npcs=npcs,
            resources=llm_result.resources,
        )


def _apply_absent_turns(
    *,
    current_npcs: list[NPCState],
    updated_npcs: list[_NPCStateUpdate],
    mentioned_npcs: list[str],
) -> list[NPCState]:
    old_absent = {
        npc.name: npc.absent_turns
        for npc in current_npcs
    }
    mentioned = {
        name.strip()
        for name in mentioned_npcs
        if name.strip()
    }

    cleaned: list[NPCState] = []
    for npc in updated_npcs:
        if npc.name not in old_absent:
            absent_turns = 0
        elif npc.name in mentioned:
            absent_turns = 0
        else:
            absent_turns = old_absent.get(npc.name, 0) + 1

        updated = NPCState(**npc.model_dump(), absent_turns=absent_turns)
        if updated.absent_turns < _STALE_NPC_THRESHOLD:
            cleaned.append(updated)
    return cleaned
