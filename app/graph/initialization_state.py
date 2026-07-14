from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.core_memory import CoreMemory, InitConfig, StoryDirectionResult
from app.domain.current_state import CurrentState
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.services import RuntimeServices


class InitializationEvent(TypedDict, total=False):
    step: str
    data: dict[str, Any]


class InitializationState(TypedDict, total=False):
    runtime_services: RuntimeServices
    events: Annotated[list[InitializationEvent], add]
    trace_id: str
    config: InitConfig
    core_memory: CoreMemory
    current_state: CurrentState
    story_direction: StoryDirectionResult
    opening_scene: SceneWriterResult
    turn_id: str
    status: str
