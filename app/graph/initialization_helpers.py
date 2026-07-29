from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from app.core.llm.token_usage import get_trace_token_usage
from app.domain.core_memory import (
    CampaignSetupAgent,
    CampaignSetupProposal,
    InitConfig,
    build_identity_core_memory,
)
from app.runtime.initialization import commit_campaign_setup
from app.runtime.serialization import model_data
from app.runtime.session import PendingOpening, get_campaign_runtime

from .initialization_state import InitializationState


def initialization_gateway(state: InitializationState):
    return state["runtime_services"].gateway(session_type="init")


def preview_initialization_config(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        config = InitConfig.model_validate(payload)
        identity_memory = build_identity_core_memory(config)
    except ValidationError as exc:
        return {
            "valid": False,
            "validation_errors": exc.errors(include_url=False),
            "config": payload,
            "identity_core_memory": None,
        }
    return {
        "valid": True,
        "validation_errors": [],
        "config": model_data(config),
        "identity_core_memory": model_data(identity_memory),
    }


def generate_campaign_setup_stream(payload: dict[str, Any]):
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        if runtime.initialization.load() is not None:
            raise RuntimeError("Campaign setup is already committed")
        config = InitConfig.model_validate(payload)
        trace_id = str(uuid.uuid4())
        yield {
            "step": "campaign_setup_started",
            "data": {"trace_id": trace_id},
        }
        proposal = CampaignSetupAgent(
            runtime.services.gateway(session_type="init")
        ).generate(
            config=config,
            trace_id=trace_id,
        )
        result = {
            "config": model_data(config),
            "campaign_setup": model_data(proposal),
            "trace_id": trace_id,
        }
        yield {"step": "campaign_setup_done", "data": result}
        return result


def prepare_initialization_state(payload: dict[str, Any]) -> InitializationState:
    runtime = get_campaign_runtime()
    config_payload = payload.get("config")
    if not isinstance(config_payload, dict):
        raise ValueError("config is required")
    proposal_payload = payload.get("campaign_setup")
    if not isinstance(proposal_payload, dict):
        raise ValueError("campaign_setup is required")

    config = InitConfig.model_validate(config_payload)
    proposal = CampaignSetupProposal.model_validate(proposal_payload)
    record = commit_campaign_setup(config, proposal)
    if record.phase == "completed":
        raise RuntimeError("Initialization is already complete")
    if runtime.pending_openings.values():
        raise RuntimeError("An opening is already awaiting confirmation")

    return {
        "events": [],
        "runtime_services": runtime.services,
        "config": config,
        "campaign_setup": proposal,
        "core_memory": record.core_memory,
        "initialization_record": record,
        "foundation_trace_id": str(uuid.uuid4()),
        "opening_trace_id": str(uuid.uuid4()),
    }


def finalize_initialization_state(
    state: InitializationState,
) -> InitializationState:
    runtime = get_campaign_runtime()
    turn_id = f"opening:{uuid.uuid4()}"
    pending = PendingOpening(
        turn_id=turn_id,
        group_id=turn_id,
        version_index=1,
        initialization_fingerprint=state["initialization_record"].fingerprint,
        config=state["config"],
        current_state=state["current_state"],
        scene_result=state["opening_scene"],
        token_usage={
            "generation": get_trace_token_usage(state["opening_trace_id"]),
            "confirmation": None,
        },
    )
    record = runtime.initialization.load()
    if record is None or record.foundation is None:
        raise RuntimeError("Opening Foundation was not persisted")
    runtime.initialization.save(record.model_copy(update={"phase": "opening_pending"}))
    try:
        runtime.pending_openings.add(pending)
    except BaseException:
        runtime.initialization.save(
            record.model_copy(update={"phase": "foundation_ready"})
        )
        raise
    return {
        **state,
        "turn_id": turn_id,
        "status": "awaiting_confirm",
        "events": [
            *state.get("events", []),
            {
                "step": "initialization_pending",
                "data": {"turn_id": turn_id, "status": "awaiting_confirm"},
            },
        ],
    }


def initialization_result(state: InitializationState) -> dict[str, Any]:
    return {
        "turn_id": state["turn_id"],
        "config": model_data(state["config"]),
        "campaign_setup": model_data(state["campaign_setup"]),
        "core_memory": model_data(state["core_memory"]),
        "current_state": model_data(state["current_state"]),
        "opening_scene": model_data(state["opening_scene"]),
        "status": state["status"],
    }
