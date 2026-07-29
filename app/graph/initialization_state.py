from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.core_memory import CampaignSetupProposal, CoreMemory, InitConfig
from app.domain.current_state import CurrentState
from app.domain.opening_foundation import OpeningFoundation
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.initialization import InitializationRecord
from app.runtime.services import RuntimeServices


class InitializationEvent(TypedDict, total=False):
    step: str
    data: dict[str, Any]


class InitializationState(TypedDict, total=False):
    runtime_services: RuntimeServices
    events: Annotated[list[InitializationEvent], add]
    config: InitConfig
    campaign_setup: CampaignSetupProposal
    core_memory: CoreMemory
    initialization_record: InitializationRecord
    foundation_trace_id: str
    opening_trace_id: str
    opening_foundation: OpeningFoundation
    current_state: CurrentState
    opening_scene: SceneWriterResult
    turn_id: str
    status: str
