import json
import os
import tempfile
from types import SimpleNamespace

import pytest

from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
    LLMProviderError,
    PromptNotFoundError,
)
from app.core.llm.gateway import LLMGateway
from app.core.llm.mock import MockLLM
from app.core.llm.providers import (
    MockProvider,
    ProviderResult,
    _extract_usage,
)
from app.core.llm.prompts import PromptLoader
from app.core.llm.schemas import EchoResult
from app.core.llm.tracing import NullTraceLogger, TraceLogger
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
        finally:
            os.unlink(trace_path)

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
        finally:
            os.unlink(trace_path)

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
        usage = _extract_usage(SimpleNamespace(total_tokens=12))

        assert usage == {"total_tokens": 12}

    def test_usage_extraction_returns_none_when_fields_are_missing(self):
        assert _extract_usage(SimpleNamespace()) is None


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
        assert "memory_draft_extract" in tasks
        assert "memory_batch_classify" in tasks
