import json
import re
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import (
    CurrentState,
    NPCState,
    PHASE_ORDER,
    PlayerState,
    ResourcesState,
    SceneState,
    WorldClock,
)
from pydantic import BaseModel, Field


_STALE_NPC_THRESHOLD = 3
_MAX_WORLD_CLOCK_RETRIES = 2


class _SceneStateUpdate(BaseModel):
    location: str
    participants: list[str] = Field(default_factory=list)
    atmosphere: str | None = None


class _WorldClockUpdate(BaseModel):
    reason: str | None = None
    day: int = Field(default=1, ge=1)
    phase_index: int = Field(default=1, ge=0, le=5)


class _NPCStateUpdate(BaseModel):
    name: str
    role: str | None = None
    location: str | None = None
    condition: list[str] = Field(default_factory=list)
    attitude_to_player: str | None = None
    current_intent: str | None = None


class _CurrentStateUpdate(BaseModel):
    scene: _SceneStateUpdate
    world_clock: _WorldClockUpdate
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
        scene_facts: str,
        *,
        core_memory: CoreMemory,
        npc_aliases: dict[str, list[str]] | None = None,
        trace_id: str | None = None,
    ) -> CurrentState:
        state_for_llm = current_state.model_copy(deep=True)
        state_data = state_for_llm.model_dump()
        state_data["scene"].pop("scene_id", None)
        for npc in state_data.get("npcs", []):
            npc.pop("absent_turns", None)

        schema = _CurrentStateUpdate.model_json_schema()

        clock_validation_feedback = "无；这是首次输出。"
        llm_result: _CurrentStateUpdate | None = None
        world_clock: WorldClock | None = None
        for attempt in range(_MAX_WORLD_CLOCK_RETRIES + 1):
            llm_result = cast(
                _CurrentStateUpdate,
                self.llm.run(
                    task="state_update",
                    input_data={
                        "core_memory_json": core_memory.model_dump_json(indent=2),
                        "current_state_json": json.dumps(
                            state_data, ensure_ascii=False, indent=2
                        ),
                        "scene_facts_json": scene_facts,
                        "clock_validation_feedback": clock_validation_feedback,
                        "output_schema_json": json.dumps(
                            schema, ensure_ascii=False, indent=2
                        ),
                    },
                    output_schema=_CurrentStateUpdate,
                    trace_id=trace_id,
                ),
            )
            proposed_clock = _world_clock_from_update(llm_result.world_clock)
            if not _clock_moves_backward(current_state.world_clock, proposed_clock):
                world_clock = proposed_clock
                break
            if attempt == _MAX_WORLD_CLOCK_RETRIES:
                world_clock = WorldClock(
                    day=current_state.world_clock.day + 1,
                    phase=proposed_clock.phase,
                )
                break
            clock_validation_feedback = (
                f"上一次输出 day={llm_result.world_clock.day}, "
                f"phase_index={llm_result.world_clock.phase_index}，对应"
                f"{proposed_clock.label}，早于当前时间"
                f"{current_state.world_clock.label}。这是非法倒退；请重新检查跨日，"
                "输出不早于 CurrentState 的最终 day 和 phase_index。"
            )

        assert llm_result is not None
        assert world_clock is not None

        if llm_result.scene.location != current_state.scene.location:
            scene_id = _increment_scene_id(current_state.scene.scene_id)
        else:
            scene_id = current_state.scene.scene_id
        npcs = _apply_absent_turns(
            current_npcs=current_state.npcs,
            updated_npcs=llm_result.npcs,
            mentioned_npcs=llm_result.mentioned_npcs,
            npc_aliases=npc_aliases,
        )

        return CurrentState(
            scene=SceneState(
                scene_id=scene_id,
                location=llm_result.scene.location,
                participants=llm_result.scene.participants,
                atmosphere=llm_result.scene.atmosphere,
            ),
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
    npc_aliases: dict[str, list[str]] | None = None,
) -> list[NPCState]:
    old_absent = {
        npc.name: npc.absent_turns
        for npc in current_npcs
    }
    variant_map = _build_alias_variant_map(npc_aliases or {})
    mentioned = {
        _norm_name(name)
        for name in mentioned_npcs
        if name and name.strip()
    }

    cleaned: list[NPCState] = []
    for npc in updated_npcs:
        norm_name = _norm_name(npc.name)
        variants = variant_map.get(norm_name, {norm_name})
        is_mentioned = bool(variants & mentioned)

        if npc.name not in old_absent:
            absent_turns = 0
        elif is_mentioned:
            absent_turns = 0
        else:
            absent_turns = old_absent.get(npc.name, 0) + 1

        updated = NPCState(**npc.model_dump(), absent_turns=absent_turns)
        if updated.absent_turns < _STALE_NPC_THRESHOLD:
            cleaned.append(updated)
    return cleaned


def _norm_name(value: str) -> str:
    return str(value).strip().casefold()


def _build_alias_variant_map(
    npc_aliases: dict[str, list[str]],
) -> dict[str, set[str]]:
    variant_map: dict[str, set[str]] = {}
    for name, aliases in npc_aliases.items():
        variants = {_norm_name(name)}
        for alias in aliases or []:
            if alias and str(alias).strip():
                variants.add(_norm_name(alias))
        variants.discard("")
        for variant in variants:
            variant_map.setdefault(variant, set()).update(variants)
    return variant_map


def _world_clock_from_update(update: _WorldClockUpdate) -> WorldClock:
    return WorldClock(day=update.day, phase=PHASE_ORDER[update.phase_index])


def _clock_moves_backward(current_clock: WorldClock, proposed_clock: WorldClock) -> bool:
    return _clock_index(proposed_clock) < _clock_index(current_clock)


def _clock_index(clock: WorldClock) -> int:
    return (clock.day - 1) * len(PHASE_ORDER) + PHASE_ORDER.index(clock.phase)
