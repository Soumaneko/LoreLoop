from __future__ import annotations

from app.core.llm.token_usage import get_trace_token_usage
from app.domain.core_memory import build_initial_current_state
from app.domain.opening_foundation import OpeningFoundationAgent
from app.domain.scene_writer.opening import OpeningSceneAgent
from app.runtime.initialization import InitializationRecord
from app.runtime.serialization import model_data
from app.runtime.session import get_campaign_runtime

from .initialization_helpers import initialization_gateway
from .initialization_state import InitializationState


def opening_foundation_node(
    state: InitializationState,
) -> InitializationState:
    runtime = get_campaign_runtime()
    record = runtime.initialization.load()
    if record is None:
        raise RuntimeError("Committed campaign setup is missing")
    if record.fingerprint != state["initialization_record"].fingerprint:
        raise RuntimeError("Initialization fingerprint changed during generation")

    foundation = record.foundation
    reused = foundation is not None
    if foundation is None:
        foundation = OpeningFoundationAgent(
            initialization_gateway(state)
        ).generate(
            core_memory=state["core_memory"],
            trace_id=state["foundation_trace_id"],
        )
        record = record.model_copy(update={
            "phase": "foundation_ready",
            "foundation": foundation,
            "foundation_token_usage": get_trace_token_usage(
                state["foundation_trace_id"]
            ),
        })
        runtime.initialization.save(record)

    current_state = build_initial_current_state(
        state["config"],
        location=foundation.starting_location.name,
        currency_unit=state["campaign_setup"].currency_unit,
        inventory=foundation.initial_resources.inventory,
        money=foundation.initial_resources.money,
        fixed_assets=foundation.initial_resources.fixed_assets,
    )
    return {
        "initialization_record": record,
        "opening_foundation": foundation,
        "current_state": current_state,
        "events": [{
            "step": "opening_foundation_ready",
            "data": {"reused": reused},
        }],
    }


def opening_scene_node(state: InitializationState) -> InitializationState:
    opening_scene = OpeningSceneAgent(initialization_gateway(state)).generate(
        current_state=state["current_state"],
        core_memory=state["core_memory"],
        opening_foundation=state["opening_foundation"],
        trace_id=state["opening_trace_id"],
    )
    return {
        "opening_scene": opening_scene,
        "events": [{
            "step": "opening_scene_done",
            "data": {"opening_scene": model_data(opening_scene)},
        }],
    }
