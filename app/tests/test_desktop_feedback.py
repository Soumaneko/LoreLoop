import json
import sys

from app.core.diagnostics import DiagnosticLogger, record_local_failure
from app.player_server import _project_client_error, PlayerHandler
from app.runtime.operations import OperationManager


def test_gateway_entrypoints_redact_their_configured_credentials(tmp_path, monkeypatch):
    from app.core.llm import config, providers
    from app.core.diagnostics import diagnostic_path_for_trace

    env_secret = "environment-provider-credential"
    saved_secret = "saved-provider-credential"
    monkeypatch.setenv("OPENAI_API_KEY", env_secret)
    monkeypatch.setattr(providers, "PydanticAIProvider", lambda **kwargs: object())
    trace_path = tmp_path / "trace.jsonl"
    gateway = config.create_gateway(log_path=str(trace_path))
    factory = config.SharedGatewayFactory(
        config=config.GatewayConfig(api_key=saved_secret),
        diagnostic_path=str(tmp_path / "shared.jsonl"),
    )
    # Exercise both real construction paths without contacting a provider.
    for logger, secret in [(gateway.tracer.diagnostics, env_secret), (factory.diagnostic_logger, saved_secret)]:
        assert logger.secrets == (secret,)
        record_local_failure(RuntimeError(f"failure: {secret}"), source="gateway", logger=logger)
        entry = logger.read()[0]
        assert secret not in json.dumps(entry)
        assert "[REDACTED]" in entry["message"]
    assert diagnostic_path_for_trace(trace_path).is_file()


def test_failure_is_recorded_without_console_and_configured_secrets(tmp_path, monkeypatch):
    from app.runtime.session import get_campaign_runtime

    secret = "arbitrary-provider-credential"
    log = DiagnosticLogger(tmp_path / "diagnostics.jsonl", secrets=(secret,))
    monkeypatch.setattr(get_campaign_runtime(), "diagnostics", log)
    with monkeypatch.context() as isolated:
        isolated.setattr(sys, "stdout", None)
        isolated.setattr(sys, "stderr", None)
        status, code, message = _project_client_error(
            RuntimeError(f"failure {secret} Authorization: Bearer header-token" + " {'Authorization': 'Bearer quoted-token'}"), label="save import",
        )
    assert status == 500 and code == "internal_error"
    assert str(tmp_path / "diagnostics.jsonl") in message
    assert "诊断编号" in message
    entry = log.read()[0]
    assert entry["source"] == "save import"
    assert entry["details"]["diagnostic_id"] in message
    serialized = json.dumps(entry)
    assert secret not in serialized
    assert "header-token" not in serialized
    assert "quoted-token" not in serialized
    assert "RuntimeError" in serialized


def test_failed_preferred_log_uses_real_fallback(tmp_path, monkeypatch):
    from app.core import diagnostics

    log = DiagnosticLogger(tmp_path / "preferred.jsonl")
    def denied(**kwargs):
        raise PermissionError("log denied")
    monkeypatch.setattr(log, "error", denied)
    monkeypatch.setattr(diagnostics.tempfile, "gettempdir", lambda: str(tmp_path))
    message = record_local_failure(RuntimeError("original failure"), source="test", logger=log)
    fallback = tmp_path / "LoreLoop-diagnostics.jsonl"
    assert str(fallback) in message
    assert "original failure" in fallback.read_text()


def test_failed_logging_does_not_mask_operation_failure(tmp_path, monkeypatch):
    def denied(self, **kwargs):
        raise PermissionError("no log writable")
    log = DiagnosticLogger(tmp_path / "unwritable.jsonl")
    monkeypatch.setattr(DiagnosticLogger, "error", denied)
    manager = OperationManager("save-1", diagnostics=log)
    def stream():
        raise PermissionError("cannot save campaign")
        yield
    try:
        operation_id = manager.submit_stream("confirmation", stream)
        failed = manager.wait(operation_id, timeout=5)
        assert failed.status == "failed"
        code, message = PlayerHandler._project_operation_failure(failed)
        assert code == "file_access_failed"
        assert "访问被拒绝" in message
        assert "诊断记录未能保存" in message
        public = PlayerHandler._player_operation(failed.as_dict())
        assert public["error"]["message"] == message
        assert "error_type" not in public
    finally:
        manager.shutdown()


def test_operational_notices_explain_partial_work_without_private_payloads():
    from app.runtime.runtime_feedback import notices_from_event
    for step, data, expected in [
        ("finish", {"warning_codes": ["confirmation_maintenance_schedule_failed"]}, "回合已确认"),
        ("scene_facts_done", {"warning_codes": ["scene_facts_degraded"]}, "场景事实"),
        ("canon_queries_generated", {"warning_codes": ["canon_query_fallback"]}, "历史查询"),
        ("feedback_rewrite_strategy_selected", {"reason_code": "strategy_unavailable"}, "原文已保留"),
        ("npc_profile_updated", {"warnings": [{"code": "invalid_update_entry", "npc_name": "private identity"}]}, "部分人物档案更新"),
    ]:
        notices = notices_from_event(step, data)
        assert any(expected in notice["message"] for notice in notices)
        assert "private identity" not in str(notices)


def test_runtime_notice_actions_match_confirmation_and_maintenance_phases():
    from app.runtime.runtime_feedback import notices_from_event

    facts = notices_from_event("scene_facts_done", {"warning_codes": ["scene_facts_degraded"]})[0]
    assert "世界状态和人物档案" in facts["message"]
    assert "编辑正文" not in facts["message"]
    for step in ("director_agent_staged", "director_agent_done"):
        decision = notices_from_event(step, {"warning_codes": ["director_decision_fallback"]})[0]
        assert "撤销" not in decision["message"]
        assert "编辑正文" not in decision["message"]
    maintenance = notices_from_event("maintenance_finished", {"warning_codes": ["scene_summary_condense_skipped"]})[0]
    assert "原有摘要仍保留" in maintenance["message"]
    assert "下次确认" in maintenance["message"]
    assert "撤销" not in maintenance["message"]
