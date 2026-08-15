import pytest

import tempfile
from concurrent.futures import CancelledError
from app.core.cancellation import CancellationToken
from app.core.cancellation import bind_cancellation_token
from app.core.cancellation import reset_cancellation_token
from app.core.llm.errors import LLMOutputParseError
from app.core.llm.errors import LLMProviderError
from app.core.llm.errors import PromptNotFoundError
from app.core.llm.gateway import LLMGateway
from app.core.llm.mock import MockLLM
from app.core.llm.providers import MockProvider
from app.core.llm.providers import ProviderResult
from app.core.llm.prompts import PromptLoader
from app.core.llm.schemas import EchoResult
from app.core.llm.tracing import NullTraceLogger

from app.tests.factories.gateways import (
    make_llm_gateway,
)


class TestLLMGatewayNormalFlow:
    def test_normal_json_output(self):
        mock = MockLLM()
        mock.set_responses(['{"text": "hello", "length": 5}'])
        gateway = make_llm_gateway(mock)

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
        gateway = make_llm_gateway(mock)
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
        gateway = make_llm_gateway(mock)

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
        gateway = make_llm_gateway(mock)

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
        gateway = make_llm_gateway(mock)

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
        gateway = make_llm_gateway(mock)

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
        gateway = make_llm_gateway(mock)

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
            max_retries=0,
        )

        assert result.text == "hello"

    def test_permanent_provider_error_is_not_retried(self):
        calls = []

        class PermanentProvider(MockProvider):
            def generate(self, *_args, **_kwargs):
                calls.append("called")
                raise LLMProviderError("Authentication failed: 401")

        gateway = LLMGateway(
            provider=PermanentProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )

        with pytest.raises(LLMProviderError, match="Authentication failed"):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
                max_retries=2,
            )

        assert calls == ["called"]

    def test_permanent_provider_error_diagnostic_is_error(self):
        class CapturingDiagnostics:
            def __init__(self):
                self.entries = []

            def warning(self, **fields):
                self.entries.append({"severity": "warning", **fields})

            def error(self, **fields):
                self.entries.append({"severity": "error", **fields})

        class CapturingTrace:
            def __init__(self):
                self.diagnostics = CapturingDiagnostics()

            def log(self, **_kwargs):
                pass

        class PermanentProvider(MockProvider):
            def generate(self, *_args, **_kwargs):
                raise LLMProviderError("Authentication failed: 401")

        trace = CapturingTrace()
        gateway = LLMGateway(
            provider=PermanentProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=trace,
        )

        with pytest.raises(LLMProviderError, match="Authentication failed"):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
                max_retries=2,
            )

        assert trace.diagnostics.entries[-1]["severity"] == "error"

    def test_transient_provider_error_retries_until_exhausted(self):
        calls = []

        class TransientProvider(MockProvider):
            def generate(self, *_args, **_kwargs):
                calls.append("called")
                raise LLMProviderError("Rate limit exceeded: 429")

        gateway = LLMGateway(
            provider=TransientProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )

        with pytest.raises(LLMProviderError, match="Rate limit exceeded"):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "hello"},
                output_schema=EchoResult,
                max_retries=2,
            )

        assert calls == ["called", "called", "called"]

    def test_unexpected_programming_error_is_not_retried_or_retyped(self, monkeypatch):
        mock = MockLLM()
        mock.set_responses(['{"text": "hello", "length": 5}'])
        gateway = make_llm_gateway(mock)
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
