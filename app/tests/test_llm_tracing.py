import pytest

import os
import tempfile
from app.core.llm.errors import LLMOutputParseError
from app.core.llm.gateway import LLMGateway
from app.core.llm.mock import MockLLM
from app.core.llm.providers import MockProvider
from app.core.llm.providers import ProviderResult
from app.core.llm.schemas import EchoResult
from app.core.llm.tracing import TRACE_LOG_MAX_BYTES
from app.core.llm.tracing import NullTraceLogger
from app.core.llm.tracing import TraceLogger
from app.core.llm.token_usage import bind_token_usage_sink
from app.core.llm.token_usage import reset_token_usage_sink


class TestTraceLogging:
    def test_trace_log_default_limit_is_fifty_mib(self):
        assert TRACE_LOG_MAX_BYTES == 50 * 1024 * 1024

    def test_mock_gateway_uses_null_tracer_by_default(self):
        mock = MockLLM()
        mock.set_responses(['{"text": "hello", "length": 5}'])
        gateway = mock.build_gateway()

        gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
            trace_id="mock-trace",
        )

        assert isinstance(gateway.tracer, NullTraceLogger)
        assert gateway.tracer.read_traces(trace_id="mock-trace") == []

    def test_trace_written_on_success(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            trace_path = f.name

        try:
            tracer = TraceLogger(log_path=trace_path)
            mock = MockLLM()
            mock.set_responses(['{"text": "hello", "length": 5}'])
            gateway = LLMGateway(
                provider=mock.provider,
                prompt_loader=mock.prompt_loader,
                tracer=tracer,
            )

            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
                trace_id="test-trace-123",
            )

            traces = tracer.read_traces(trace_id="test-trace-123")
            assert len(traces) == 1
            trace = traces[0]
            assert trace["trace_id"] == "test-trace-123"
            assert trace["task"] == "echo_structured_test"
            assert trace["schema_valid"] is True
            assert trace["error"] is None
            assert trace["parsed_output"] == {"text": "hello", "length": 5}
            assert trace["input_data"] == {"text": "hello"}
            assert trace["latency_ms"] >= 0
            assert len(trace["prompt_fingerprint"]) == 64
        finally:
            os.unlink(trace_path)

    def test_trace_publishes_player_usage_without_prompt_or_output(self, tmp_path):
        class _UsageProvider(MockProvider):
            def generate(self, *_args, **_kwargs):
                return ProviderResult(
                    content='{"text": "hello", "length": 5}',
                    usage={
                        "prompt_tokens": 12,
                        "completion_tokens": 4,
                        "total_tokens": 16,
                    },
                )

        events = []
        context_token = bind_token_usage_sink(events.append)
        try:
            gateway = LLMGateway(
                provider=_UsageProvider(),
                prompt_loader=MockLLM().prompt_loader,
                tracer=TraceLogger(
                    log_path=str(tmp_path / "llm_traces.jsonl")
                ),
            )
            gateway.set_trace_context(session_type="generation")

            gateway.run(
                task="echo_structured_test",
                input_data={"text": "private prompt input"},
                output_schema=EchoResult,
            )
        finally:
            reset_token_usage_sink(context_token)

        assert len(events) == 1
        assert events[0] == {
            "_call_id": events[0]["_call_id"],
            "task": "echo_structured_test",
            "call_index": 1,
            "attempt": 1,
            "input_tokens": 12,
            "output_tokens": 4,
            "total_tokens": 16,
            "cached_tokens": None,
            "cache_creation_tokens": None,
            "cache_hit_rate": None,
            "reported": True,
        }


        assert events[0]["_call_id"]
        assert "private prompt input" not in str(events)
        assert "hello" not in str(events)

    def test_trace_written_on_failure(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            trace_path = f.name

        try:
            tracer = TraceLogger(log_path=trace_path)
            mock = MockLLM()
            mock.set_responses(["not valid json"])
            gateway = LLMGateway(
                provider=mock.provider,
                prompt_loader=mock.prompt_loader,
                tracer=tracer,
            )

            with pytest.raises(LLMOutputParseError):
                gateway.run(
                    task="echo_structured_test",
                    input_data={"text": "hello"},
                    output_schema=EchoResult,
                    max_retries=0,
                    trace_id="test-trace-fail",
                )

            traces = tracer.read_traces(trace_id="test-trace-fail")
            assert len(traces) == 1
            trace = traces[0]
            assert trace["schema_valid"] is False
            assert trace["error"] is not None
            diagnostics = tracer.diagnostics.read(trace_id="test-trace-fail")
            assert len(diagnostics) == 1
            assert diagnostics[0]["severity"] == "error"
            assert diagnostics[0]["code"] == "llm_output_parse_failed"
        finally:
            os.unlink(trace_path)
            if os.path.exists(tracer.diagnostics.log_path):
                os.unlink(tracer.diagnostics.log_path)

    def test_trace_log_keeps_one_bounded_file_and_newest_complete_entries(
        self,
        tmp_path,
    ):
        trace_path = tmp_path / "llm_traces.jsonl"
        tracer = TraceLogger(log_path=str(trace_path), max_bytes=4096)

        for index in range(20):
            tracer.log(
                trace_id=f"trace-{index}",
                task="bounded",
                model="test-model",
                prompt_version="v1",
                input_data={"turn": index, "context": "中" * 180},
                raw_output="out" * 180,
                parsed_output={"turn": index},
                schema_valid=True,
                latency_ms=1.0,
                error=None,
            )

        assert trace_path.stat().st_size <= 4096
        traces = tracer.read_traces()
        assert traces
        assert traces[-1]["trace_id"] == "trace-19"
        assert traces[0]["trace_id"] != "trace-0"
        assert [
            path for path in tmp_path.iterdir()
            if path.name.startswith("llm_traces")
        ] == [trace_path]

    def test_single_oversized_trace_is_replaced_by_valid_marker(self, tmp_path):
        trace_path = tmp_path / "llm_traces.jsonl"
        tracer = TraceLogger(log_path=str(trace_path), max_bytes=1024)

        tracer.log(
            trace_id="oversized",
            task="bounded",
            model="test-model",
            prompt_version="v1",
            input_data={"context": "x" * 5000},
            raw_output="y" * 5000,
            parsed_output={"result": "z" * 5000},
            schema_valid=True,
            latency_ms=1.0,
            error=None,
        )

        assert trace_path.stat().st_size <= 1024
        traces = tracer.read_traces()
        assert len(traces) == 1
        marker = traces[0]
        assert marker == {
            "trace_id": "oversized",
            "task": "bounded",
            "model": "test-model",
            "prompt_version": "v1",
            "schema_valid": True,
            "latency_ms": 1.0,
            "timestamp": marker["timestamp"],
            "trace_entry_truncated": True,
            "original_entry_bytes": marker["original_entry_bytes"],
        }

    def test_trace_id_auto_generated(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            trace_path = f.name

        try:
            tracer = TraceLogger(log_path=trace_path)
            mock = MockLLM()
            mock.set_responses(['{"text": "hello", "length": 5}'])
            gateway = LLMGateway(
                provider=mock.provider,
                prompt_loader=mock.prompt_loader,
                tracer=tracer,
            )

            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
            )

            traces = tracer.read_traces()
            assert len(traces) == 1
            assert len(traces[0]["trace_id"]) == 32
        finally:
            os.unlink(trace_path)

    def test_trace_logs_cached_tokens_and_hit_rate(self, tmp_path):
        tracer = TraceLogger(log_path=str(tmp_path / "llm_traces.jsonl"))
        tracer.log(
            trace_id="test-cache-trace",
            task="echo_test",
            model="gpt-4o",
            prompt_version="v1",
            input_data={"text": "hi"},
            raw_output='{"text": "hi"}',
            parsed_output={"text": "hi"},
            schema_valid=True,
            latency_ms=10.0,
            error=None,
            token_usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "cached_tokens": 72,
                "cache_creation_tokens": 10,
            },
        )

        traces = tracer.read_traces(trace_id="test-cache-trace")
        assert len(traces) == 1
        assert traces[0]["cached_tokens"] == 72
        assert traces[0]["cache_creation_tokens"] == 10
        assert traces[0]["cache_hit_rate"] == 0.72

    def test_trace_token_usage_accumulation_with_cache(self, tmp_path):
        from app.core.llm.token_usage import (
            bind_token_usage_sink,
            bind_token_usage_trace,
            get_trace_token_usage,
            reset_token_usage_sink,
            reset_token_usage_traces,
        )

        trace_id = "trace-accumulate-test"
        events = []
        token = bind_token_usage_sink(events.append)
        try:
            assert bind_token_usage_trace(trace_id) is True
            tracer = TraceLogger(log_path=str(tmp_path / "llm_traces.jsonl"))
            tracer.log(
                trace_id=trace_id,
                task="task_1",
                model="gpt-4o",
                prompt_version="v1",
                input_data={},
                raw_output="{}",
                parsed_output={},
                schema_valid=True,
                latency_ms=10.0,
                error=None,
                token_usage={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cached_tokens": 50,
                },
                session_type="generation",
            )
            tracer.log(
                trace_id=trace_id,
                task="task_2",
                model="gpt-4o",
                prompt_version="v1",
                input_data={},
                raw_output="{}",
                parsed_output={},
                schema_valid=True,
                latency_ms=10.0,
                error=None,
                token_usage={
                    "input_tokens": 100,
                    "output_tokens": 30,
                    "cached_tokens": 70,
                },
                session_type="generation",
            )

            usage = get_trace_token_usage(trace_id)
            assert usage is not None
            assert usage["input_tokens"] == 200
            assert usage["output_tokens"] == 50
            assert usage["total_tokens"] == 250
            assert usage["cached_tokens"] == 120
            assert usage["cache_hit_rate"] == 0.6  # 120 / 200
        finally:
            reset_token_usage_sink(token)
            reset_token_usage_traces({trace_id})


class TestCallLifecycleEvents:
    def test_lifecycle_events_use_independent_file(self, tmp_path):
        tracer = TraceLogger(log_path=str(tmp_path / "llm_traces.jsonl"))
        base = {
            "trace_id": "lifecycle-file",
            "call_id": "call-1",
            "task": "echo_structured_test",
            "attempt": 1,
            "model": "test-model",
            "protocol": "openai_chat",
            "started_at": 123.0,
        }
        tracer.log_call_event(
            "llm_call_started", elapsed_ms=0.0, timeout_seconds=120.0, **base
        )
        tracer.log_call_event(
            "llm_call_timed_out",
            elapsed_ms=120.5,
            timeout_seconds=120.0,
            error_code="timeout",
            transport_attempts=1,
            **base,
        )

        events = tracer.read_call_events(trace_id="lifecycle-file")

        assert [event["event"] for event in events] == [
            "llm_call_started",
            "llm_call_timed_out",
        ]
        assert events[0]["call_id"] == "call-1"
        assert events[1]["error_code"] == "timeout"
        assert events[1]["transport_attempts"] == 1
        assert "input_data" not in events[0]
        assert "raw_output" not in events[0]
        # The complete-trace file keeps one-record-per-call semantics.
        assert tracer.read_traces(trace_id="lifecycle-file") == []
        assert (tmp_path / "llm_call_events.jsonl").exists()

    def test_lifecycle_events_reject_unknown_events(self, tmp_path):
        tracer = TraceLogger(log_path=str(tmp_path / "llm_traces.jsonl"))

        with pytest.raises(ValueError, match="unsupported call event"):
            tracer.log_call_event(
                "llm_call_unknown",
                trace_id="t",
                call_id="c",
                task="task",
                attempt=1,
                model="m",
                protocol="openai_chat",
                started_at=1.0,
                elapsed_ms=0.0,
            )

