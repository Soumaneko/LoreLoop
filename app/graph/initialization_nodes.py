from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.llm.token_usage import (
    bind_token_usage_trace,
    get_trace_token_usage,
)
from app.domain.core_memory import build_initial_current_state
from app.domain.opening_foundation import OpeningFoundationAgent
from app.domain.opening_setting import OpeningSettingAgent
from app.domain.scene_writer.opening import OpeningSceneAgent
from app.domain.world_setting import WorldSettingCreator
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
    with state["runtime_services"].stores.current_state() as state_store:
        state_store.save(current_state)
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


def opening_setting_node(state: InitializationState) -> InitializationState:
    runtime = get_campaign_runtime()
    with state["runtime_services"].stores.world_setting() as store:
        existing = store.list_all()
    if existing:
        return {
            "opening_settings": existing,
            "events": [{
                "step": "opening_settings_done",
                "data": {"reused": True, "count": len(existing)},
            }],
        }

    bind_token_usage_trace(state["settings_trace_id"])
    gateway = initialization_gateway(state)
    specs = OpeningSettingAgent(gateway).generate(
        foundation=state["opening_foundation"],
        core_memory=state["core_memory"],
        trace_id=state["settings_trace_id"],
    ).specs

    def _create(spec):
        return WorldSettingCreator(gateway).create(
            kind=spec.kind,
            guidance=spec.setting_description,
            core_memory=state["core_memory"],
            existing_settings=existing,
            turn_index=0,
            trace_id=state["settings_trace_id"],
        )

    with ThreadPoolExecutor(
        max_workers=min(3, len(specs)),
        thread_name_prefix="opening-setting",
    ) as executor:
        settings = [future.result() for future in [
            executor.submit(_create, spec) for spec in specs
        ]]
    if len({setting.name for setting in settings}) != len(settings):
        raise RuntimeError("opening settings must have unique names")

    with state["runtime_services"].stores.world_setting() as store:
        store.save_many(settings)
    return {
        "opening_settings": settings,
        "events": [{
            "step": "opening_settings_done",
            "data": {"reused": False, "count": len(settings)},
        }],
    }
