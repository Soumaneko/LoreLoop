from __future__ import annotations

import uuid

from app.core.cancellation import commit_if_not_cancelled
from app.core.llm.token_usage import get_trace_token_usage
from app.domain.scene_writer.opening import OpeningSceneAgent
from app.runtime.serialization import model_data
from app.runtime.session import PendingOpening, get_campaign_runtime


def rewrite_opening_stream(turn_id: str):
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        source = runtime.pending_openings.get(turn_id)
        if source is None:
            raise ValueError("Pending opening not found.")

        variants = runtime.pending_openings.variants(source.group_id)
        version_index = max(item.version_index for item in variants) + 1
        initialization = runtime.initialization.load()
        if (
            initialization is None
            or initialization.phase != "opening_pending"
            or initialization.foundation is None
            or initialization.fingerprint != source.initialization_fingerprint
        ):
            raise RuntimeError("Opening Foundation is missing or does not match")
        trace_id = str(uuid.uuid4())
        yield {
            "step": "opening_rewrite_started",
            "data": {
                "turn_id": turn_id,
                "group_id": source.group_id,
                "version_index": version_index,
                "trace_id": trace_id,
            },
        }

        with runtime.uow() as uow:
            core_memory = uow.core_memory.load_required()
        scene_result = OpeningSceneAgent(
            runtime.services.gateway(session_type="init")
        ).generate(
            current_state=source.current_state,
            core_memory=core_memory,
            opening_foundation=initialization.foundation,
            trace_id=trace_id,
        )

        rewritten_turn_id = f"opening:{uuid.uuid4()}"
        pending = PendingOpening(
            turn_id=rewritten_turn_id,
            group_id=source.group_id,
            version_index=version_index,
            initialization_fingerprint=source.initialization_fingerprint,
            config=source.config,
            experience_mode=source.experience_mode,
            current_state=source.current_state,
            scene_result=scene_result,
            token_usage={
                "generation": get_trace_token_usage(trace_id),
                "confirmation": None,
            },
        )
        commit_if_not_cancelled(lambda: runtime.pending_openings.add(pending))
        yield {
            "step": "scene_written",
            "data": {
                "turn_id": rewritten_turn_id,
                "group_id": source.group_id,
                "version_index": version_index,
                "turn_index": 0,
                "scene_result": model_data(scene_result),
                "token_usage": pending.token_usage,
            },
        }
