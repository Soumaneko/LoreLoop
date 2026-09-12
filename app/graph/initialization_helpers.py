from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.core.client_error import ClientValidationError
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
from app.runtime.settings import experience_mode

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


_PLAYER_FIELD_LABELS = {
    "world_name": "世界名称",
    "world_style": "世界风格",
    "ability_system": "能力体系",
    "custom_ability_system": "自定义能力体系",
    "protagonist_name": "主角姓名",
    "protagonist_gender": "主角性别",
    "protagonist_age": "主角年龄",
    "protagonist_introduction": "主角介绍",
    "protagonist_abilities": "主角能力",
    "protagonist_abilities.name": "主角能力名称",
    "protagonist_abilities.description": "主角能力说明",
    "story_expectation": "期待的故事体验",
    "failure_severity": "失败后果尺度",
    "currency_unit": "货币单位",
    "public_power_baseline.source": "力量来源",
    "public_power_baseline.prevalence": "力量普及率",
    "public_power_baseline.learning_difficulty": "力量习得难度",
    "public_power_baseline.limitation": "力量限制",
    "peoples_and_beings": "主要存在主体",
    "major_world_conflicts": "世界主要冲突",
    "dominant_powers": "主导势力",
    "civilization_baseline": "文明与技术基准",
}


def _player_error_reason(error: Mapping[str, Any]) -> str:
    kind = error.get("type", "")
    context = error.get("ctx") or {}
    if kind in {"missing", "string_too_short"}:
        return "不能为空"
    if kind == "string_too_long":
        limit = context.get("max_length")
        return f"不能超过 {limit} 个字" if limit else "内容过长"
    if kind == "too_long":
        limit = context.get("max_length")
        return f"最多 {limit} 项" if limit else "数量过多"
    if kind == "too_short":
        limit = context.get("min_length")
        return f"至少 {limit} 项" if limit else "数量不足"
    if kind == "string_pattern_mismatch":
        return "每项必须在一行内填写"
    if kind in {"literal_error", "enum"}:
        return "取值不在允许范围内"
    if kind in {
        "int_parsing",
        "int_type",
        "string_type",
        "list_type",
        "dict_type",
        "bool_type",
    }:
        return "格式不正确"
    if kind == "value_error":
        message = str(error.get("msg", ""))
        if "blank" in message:
            return "不能为空"
        return "格式不正确"
    return "格式不正确"


def _player_input_error_message(exc: ValidationError) -> str:
    """Turn the first field error into a player-readable Chinese message."""
    errors = exc.errors(include_url=False)
    if not errors:
        return "初始化输入不合法，请检查后重试。"
    error = errors[0]
    parts = [
        str(part)
        for part in error.get("loc", ())
        if not isinstance(part, bool) and not isinstance(part, int)
    ]
    path = ".".join(parts)
    label = _PLAYER_FIELD_LABELS.get(path, path or "输入")
    return f"{label}：{_player_error_reason(error)}。"


def prepare_initialization_state(payload: dict[str, Any]) -> InitializationState:
    runtime = get_campaign_runtime()
    config_payload = payload.get("config")
    if not isinstance(config_payload, dict):
        raise ClientValidationError("初始化配置缺失，请重新填写后再提交。")
    proposal_payload = payload.get("campaign_setup")
    if not isinstance(proposal_payload, dict):
        raise ClientValidationError("公开世界惯例缺失，请先生成后再提交。")

    try:
        config = InitConfig.model_validate(config_payload)
    except ValidationError as exc:
        raise ClientValidationError(_player_input_error_message(exc)) from exc
    try:
        proposal = CampaignSetupProposal.model_validate(proposal_payload)
    except ValidationError as exc:
        raise ClientValidationError(_player_input_error_message(exc)) from exc
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
        "settings_trace_id": str(uuid.uuid4()),
        "opening_trace_id": str(uuid.uuid4()),
    }


def _combine_token_usage(*usages):
    """Sum token usage dicts from multiple traces; None-safe.

    Each usage has the shape returned by ``get_trace_token_usage``:
    ``{input_tokens, output_tokens, total_tokens, calls}``.
    """
    present = [usage for usage in usages if usage]
    if not present:
        return None
    input_tokens = sum(
        int(usage["input_tokens"]) for usage in present
        if usage.get("input_tokens") is not None
    ) or None
    output_tokens = sum(
        int(usage["output_tokens"]) for usage in present
        if usage.get("output_tokens") is not None
    ) or None
    calls: list[Any] = []
    for usage in present:
        calls.extend(usage.get("calls") or [])
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        ),
        "calls": calls,
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
        experience_mode=experience_mode(),
        current_state=state["current_state"],
        scene_result=state["opening_scene"],
        token_usage={
            "generation": _combine_token_usage(
                get_trace_token_usage(state["opening_trace_id"]),
                get_trace_token_usage(state["settings_trace_id"]),
            ),
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
