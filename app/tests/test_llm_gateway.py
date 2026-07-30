import os
import tempfile
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest

from app.core.cancellation import (
    CancellationToken,
    bind_cancellation_token,
    reset_cancellation_token,
)
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
    LLMProviderError,
    PromptNotFoundError,
    PromptRenderError,
)
from app.core.llm.gateway import LLMGateway
from app.core.llm.mock import MockLLM
from app.core.llm.providers import (
    MockProvider,
    OpenAICompatibleProvider,
    ProviderResult,
    _extract_usage,
)
from app.core.llm.prompts import PromptLoader
from app.core.llm.schemas import EchoResult
from app.core.llm.tracing import (
    TRACE_LOG_MAX_BYTES,
    NullTraceLogger,
    TraceLogger,
)
from app.core.llm.token_usage import (
    bind_token_usage_sink,
    normalize_token_usage,
    reset_token_usage_sink,
)
from app.debug_prompt_runner import FilePromptProvider


def _make_gateway(mock_llm: MockLLM) -> LLMGateway:
    return mock_llm.build_gateway()


class TestLLMGatewayNormalFlow:
    def test_normal_json_output(self):
        mock = MockLLM()
        mock.set_responses(['{"text": "hello", "length": 5}'])
        gateway = _make_gateway(mock)

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
        )

        assert isinstance(result, EchoResult)
        assert result.text == "hello"
        assert result.length == 5

    def test_model_override(self):
        mock = MockLLM()
        mock.set_responses(['{"text": "hi", "length": 2}'])
        gateway = _make_gateway(mock)
        gateway._default_model = "custom-model"

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hi"},
            output_schema=EchoResult,
            model="override-model",
        )

        assert result.text == "hi"
        assert result.length == 2

    def test_streaming_output_forwards_chunks_and_returns_validated_result(self):
        class _StreamingProvider(MockProvider):
            def stream_generate(self, *_args, **_kwargs):
                yield ProviderResult(content='{"text": "hel')
                yield ProviderResult(content='lo", "length": 5}')
                yield ProviderResult(content="", usage={"total_tokens": 12})

        gateway = LLMGateway(
            provider=_StreamingProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )
        chunks = []
        attempts = []

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
            on_chunk=chunks.append,
            on_attempt_start=attempts.append,
        )

        assert result == EchoResult(text="hello", length=5)
        assert chunks == ['{"text": "hel', 'lo", "length": 5}']
        assert attempts == [1]

    def test_streaming_cancellation_does_not_retry_or_log_an_llm_failure(self):
        calls = []

        class _StreamingProvider(MockProvider):
            def stream_generate(self, *_args, **_kwargs):
                calls.append("called")
                yield ProviderResult(content='{"text": "hel')
                yield ProviderResult(content='lo", "length": 5}')

        tracer = NullTraceLogger()
        gateway = LLMGateway(
            provider=_StreamingProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=tracer,
        )
        cancellation = CancellationToken()
        context_token = bind_cancellation_token(cancellation)
        chunks = []
        try:
            with pytest.raises(CancelledError):
                gateway.run(
                    task="echo_structured_test",
                    input_data={"text": "hello"},
                    output_schema=EchoResult,
                    on_chunk=lambda chunk: (
                        chunks.append(chunk),
                        cancellation.request(),
                    ),
                )
        finally:
            reset_cancellation_token(context_token)

        assert calls == ["called"]
        assert chunks == ['{"text": "hel']
        assert tracer.read_traces() == []


class TestLLMGatewayRetryFlow:
    def test_json_parse_failure_retries_then_succeeds(self):
        mock = MockLLM()
        mock.set_responses([
            "not valid json",
            '{"text": "hello", "length": 5}',
        ])
        gateway = _make_gateway(mock)

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
            max_retries=2,
        )

        assert result.text == "hello"
        assert result.length == 5

    def test_schema_validation_failure_retries_then_succeeds(self):
        mock = MockLLM()
        mock.set_responses([
            '{"text": 123, "length": "not_int"}',
            '{"text": "hello", "length": 5}',
        ])
        gateway = _make_gateway(mock)

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
            max_retries=2,
        )

        assert result.text == "hello"
        assert result.length == 5

    def test_output_validator_failure_retries_then_succeeds(self):
        mock = MockLLM()
        mock.set_responses([
            '{"text": "wrong", "length": 5}',
            '{"text": "hello", "length": 5}',
        ])
        gateway = _make_gateway(mock)

        def validate(result):
            if result.text != "hello":
                raise ValueError("text must be hello")

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
            output_validator=validate,
            max_retries=2,
        )

        assert result.text == "hello"
        assert result.length == 5

    def test_exceeds_max_retries_raises_error(self):
        mock = MockLLM()
        mock.set_responses(["not json", "also not json", "still bad"])
        gateway = _make_gateway(mock)

        with pytest.raises(LLMOutputParseError):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
                max_retries=2,
            )

    def test_max_retries_zero_still_tries_once(self):
        mock = MockLLM()
        mock.set_responses(['{"text": "hello", "length": 5}'])
        gateway = _make_gateway(mock)

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
            max_retries=0,
        )

        assert result.text == "hello"

    def test_unexpected_programming_error_is_not_retried_or_retyped(self, monkeypatch):
        mock = MockLLM()
        mock.set_responses(['{"text": "hello", "length": 5}'])
        gateway = _make_gateway(mock)
        calls = 0

        def _raise(_raw_output):
            nonlocal calls
            calls += 1
            raise RuntimeError("programming bug")

        monkeypatch.setattr(gateway, "_parse_json", _raise)

        with pytest.raises(RuntimeError, match="programming bug"):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
                max_retries=2,
            )

        assert calls == 1


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


class TestPromptNotFound:
    def test_prompt_file_not_found_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_loader = PromptLoader(prompts_dir=tmpdir)
            mock_provider = MockProvider()
            gateway = LLMGateway(
                provider=mock_provider,
                prompt_loader=prompt_loader,
            )

            with pytest.raises(PromptNotFoundError, match="prompt_file"):
                gateway.run(
                    task="prompt_file",
                    input_data={"text": "hello"},
                    output_schema=EchoResult,
                )

    def test_wrong_version_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_loader = PromptLoader(prompts_dir=tmpdir)
            mock_provider = MockProvider()
            gateway = LLMGateway(
                provider=mock_provider,
                prompt_loader=prompt_loader,
            )

            with pytest.raises(PromptNotFoundError):
                gateway.run(
                    task="echo_structured_test",
                    input_data={"text": "hello"},
                    output_schema=EchoResult,
                    # version is internally "v1", but no v1 file exists in tempdir
                )


class TestPromptRendering:
    def test_missing_template_variable_raises_specific_error(self):
        gateway = LLMGateway(
            provider=MockProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )

        with pytest.raises(
            PromptRenderError,
            match="Missing prompt template variable: text",
        ):
            gateway.run(
                task="echo_structured_test",
                input_data={},
                output_schema=EchoResult,
            )

    def test_json_braces_inside_input_value_are_not_reinterpreted(self):
        gateway = LLMGateway(
            provider=MockProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )

        assert gateway._build_prompt(
            "Payload: {payload}",
            {"payload": '{"nested": "{not_a_template_variable}"}'},
        ) == 'Payload: {"nested": "{not_a_template_variable}"}'

    def test_invalid_diagnostic_severity_is_rejected(self):
        gateway = LLMGateway(
            provider=MockProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )

        with pytest.raises(ValueError, match="Unsupported diagnostic severity"):
            gateway._log_diagnostic(
                severity="warn",  # type: ignore[arg-type]
                code="test",
                message="test",
                trace_id="trace",
                task="task",
                attempt=1,
            )


class TestMockProviderFeatures:
    def test_mock_provider_timeout(self):
        mock = MockLLM()
        mock.set_timeout(True)
        gateway = _make_gateway(mock)

        with pytest.raises(LLMProviderError):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
                max_retries=0,
            )

    def test_mock_provider_error(self):
        mock = MockLLM()
        mock.set_fail_provider(True)
        gateway = _make_gateway(mock)

        with pytest.raises(LLMProviderError):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
                max_retries=0,
            )

    def test_mock_provider_can_replace_real_provider(self):
        mock = MockLLM()
        mock.set_responses(['{"text": "replaced", "length": 8}'])
        gateway = _make_gateway(mock)

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
        )

        assert isinstance(gateway.provider, MockProvider)
        assert isinstance(result, EchoResult)
        assert result.text == "replaced"

    def test_mock_provider_default_response(self):
        mock = MockLLM()
        mock._provider._responses = []
        gateway = _make_gateway(mock)

        with pytest.raises(LLMOutputValidationError):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "test"},
                output_schema=EchoResult,
                max_retries=0,
            )


class TestProviderCompatibility:
    @pytest.mark.parametrize(
        ("thinking", "expected_extra_body"),
        [
            (None, None),
            ("disabled", {"thinking": {"type": "disabled"}}),
            ("enabled", {"thinking": {"type": "enabled"}}),
        ],
    )
    def test_openai_compatible_provider_sends_configured_thinking_mode(
        self,
        monkeypatch,
        thinking,
        expected_extra_body,
    ):
        captured = {}

        class _Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
                    usage=None,
                )

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions()),
        )
        monkeypatch.setattr(
            OpenAICompatibleProvider,
            "_client",
            property(lambda _self: client),
        )
        provider = OpenAICompatibleProvider(
            api_key="test",
            base_url="https://example.test",
            thinking=thinking,
        )

        provider.generate("prompt", model="model", temperature=0.2)

        if expected_extra_body is None:
            assert "extra_body" not in captured
        else:
            assert captured["extra_body"] == expected_extra_body

    def test_openai_compatible_provider_streams_content_and_usage(self, monkeypatch):
        captured = {}

        class _Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return iter([
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="一段"))],
                        usage=None,
                    ),
                    SimpleNamespace(
                        choices=[],
                        usage=SimpleNamespace(total_tokens=9),
                    ),
                ])

        client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
        monkeypatch.setattr(
            OpenAICompatibleProvider,
            "_client",
            property(lambda _self: client),
        )
        provider = OpenAICompatibleProvider("key", "https://example.test")

        chunks = list(provider.stream_generate("prompt", model="model", temperature=0.8))

        assert captured["stream"] is True
        assert captured["stream_options"] == {"include_usage": True}
        assert chunks == [
            ProviderResult(content="一段", usage=None),
            ProviderResult(content="", usage={"total_tokens": 9}),
        ]

    def test_file_prompt_provider_returns_provider_result(self):
        class _Exchange:
            def request(self, **_kwargs):
                return '{"text": "manual", "length": 6}'

        provider = FilePromptProvider(_Exchange())
        result = provider.generate(
            "prompt",
            model="manual-web",
            temperature=0.2,
        )

        assert isinstance(result, ProviderResult)
        assert result.content == '{"text": "manual", "length": 6}'
        assert result.usage is None

    def test_usage_extraction_accepts_partial_compatible_response(self):
        usage = _extract_usage(SimpleNamespace(
            input_tokens=8,
            output_tokens=4,
            total_tokens=12,
        ))

        assert usage == {
            "input_tokens": 8,
            "output_tokens": 4,
            "total_tokens": 12,
        }

    def test_usage_extraction_returns_none_when_fields_are_missing(self):
        assert _extract_usage(SimpleNamespace()) is None

    def test_public_usage_normalizes_both_provider_naming_conventions(self):
        assert normalize_token_usage({
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
        }) == {
            "input_tokens": 12,
            "output_tokens": 4,
            "total_tokens": 16,
            "reported": True,
        }
        assert normalize_token_usage({
            "input_tokens": 8,
            "output_tokens": 3,
        }) == {
            "input_tokens": 8,
            "output_tokens": 3,
            "total_tokens": None,
            "reported": True,
        }


class TestPromptLoader:
    def test_load_valid_prompt(self):
        loader = PromptLoader()
        content, version = loader.load("echo_structured_test")
        assert isinstance(content, str)
        assert len(content) > 0
        assert version == "v1"
        assert "{text}" in content

    def test_load_with_explicit_version(self):
        loader = PromptLoader()
        content, version = loader.load("echo_structured_test", version="v1")
        assert version == "v1"
        assert "{text}" in content

    def test_list_tasks(self):
        loader = PromptLoader()
        tasks = loader.list_tasks()
        assert "echo_structured_test" in tasks
        assert "continuity_project" in tasks

    def test_expands_root_relative_prompt_include(self, tmp_path):
        shared = tmp_path / "_shared"
        shared.mkdir()
        (shared / "contract.txt").write_text(
            "Shared {value}",
            encoding="utf-8",
        )
        (tmp_path / "composed_v1.md").write_text(
            "Before\n[[include: _shared/contract.txt]]\nAfter",
            encoding="utf-8",
        )

        content, _ = PromptLoader(prompts_dir=tmp_path).load("composed")

        assert content == "Before\nShared {value}\nAfter"

    @pytest.mark.parametrize(
        "include_path",
        ["/tmp/outside.txt", r"C:\tmp\outside.txt", "../outside.txt"],
    )
    def test_rejects_prompt_include_outside_root(self, tmp_path, include_path):
        (tmp_path / "composed_v1.md").write_text(
            f"[[include: {include_path}]]",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Invalid prompt include path"):
            PromptLoader(prompts_dir=tmp_path).load("composed")

    def test_rejects_circular_prompt_include(self, tmp_path):
        (tmp_path / "composed_v1.md").write_text(
            "[[include: shared.txt]]",
            encoding="utf-8",
        )
        (tmp_path / "shared.txt").write_text(
            "[[include: composed_v1.md]]",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Circular prompt include"):
            PromptLoader(prompts_dir=tmp_path).load("composed")

    def test_narrative_prompt_modes_share_contract_without_leaking_inputs(self):
        loader = PromptLoader()
        compose, _ = loader.load("narrative_compose")
        source, _ = loader.load("narrative_rewrite_from_source")
        recompose, _ = loader.load("narrative_recompose")

        shared_markers = [
            "# 权威上下文",
            "# 输出规范",
            "# 共同创作契约",
            "严禁替玩家作出输入之外的新决定",
            "CoreMemory、CurrentState、TurnResolutionResult、DirectorEvent",
        ]
        for prompt in (compose, source, recompose):
            assert "[[include:" not in prompt
            for marker in shared_markers:
                assert prompt.count(marker) == 1

        assert "Source Scene Text" not in compose
        assert "Source Scene Text" in source
        assert "Source Scene Text" not in recompose
        assert "Rewrite Requirements" not in compose
        assert "Rewrite Requirements" in source
        assert "Recompose Requirements" in recompose
