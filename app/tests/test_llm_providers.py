import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from app.core.llm.errors import LLMOutputValidationError
from app.core.llm.errors import LLMProviderError
from app.core.llm.errors import LLMTimeoutError
from app.core.llm.mock import MockLLM
from app.core.llm.providers import CONNECTION_TEST_TIMEOUT_SECONDS
from app.core.llm.providers import FORMAL_LLM_CALL_TIMEOUT_SECONDS
from app.core.llm.providers import MockProvider
from app.core.llm.providers import PydanticAIProvider
from app.core.llm.providers import ProviderResult
from app.core.llm.providers import _extract_usage
from app.core.llm.providers import _normalize_request_usage
from app.core.llm.schemas import EchoResult
from app.core.llm.token_usage import normalize_token_usage
from app.debug_prompt_runner import FilePromptProvider

from app.tests.factories.gateways import (
    make_llm_gateway,
)


class TestMockProviderFeatures:
    def test_mock_provider_timeout(self):
        mock = MockLLM()
        mock.set_timeout(True)
        gateway = make_llm_gateway(mock)

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
        gateway = make_llm_gateway(mock)

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
        gateway = make_llm_gateway(mock)

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
        gateway = make_llm_gateway(mock)

        with pytest.raises(LLMOutputValidationError):
            gateway.run(
                task="echo_structured_test",
                input_data={"text": "test"},
                output_schema=EchoResult,
                max_retries=0,
            )


class TestPydanticAIProvider:
    @pytest.mark.parametrize(
        ("protocol", "expected_model"),
        [
            ("openai_responses", "OpenAIResponsesModel"),
            ("openai_chat", "OpenAIChatModel"),
            ("anthropic_messages", "AnthropicModel"),
            ("gemini", "GoogleModel"),
        ],
    )
    def test_protocol_selects_correct_model(self, protocol, expected_model):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test/v1",
            protocol=protocol,
        )

        model = provider._build_model("test-model")

        assert type(model).__name__ == expected_model

    @pytest.mark.parametrize("protocol", [
        "openai_responses",
        "openai_chat",
        "anthropic_messages",
        "gemini",
    ])
    def test_custom_base_url_passes_through(self, protocol):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://gateway.example.test/v1",
            protocol=protocol,
        )

        model = provider._build_model("test-model")

        assert str(model._provider.base_url).rstrip("/") == (
            "https://gateway.example.test/v1"
        )

    def test_rejects_unknown_protocol(self):
        with pytest.raises(ValueError, match="protocol must be one of"):
            PydanticAIProvider(
                api_key="test",
                base_url="https://example.test",
                protocol="deepseek_native",
            )

    @pytest.mark.parametrize(
        ("thinking", "expected_extra_body"),
        [
            (None, None),
            ("disabled", {"thinking": {"type": "disabled"}}),
            ("enabled", {"thinking": {"type": "enabled"}}),
        ],
    )
    def test_openai_chat_thinking_contract(self, thinking, expected_extra_body):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            thinking=thinking,
            protocol="openai_chat",
        )

        settings = provider._model_settings(0.2)

        assert settings.get("extra_body") == expected_extra_body

    def test_openai_chat_reasoning_effort_contract(self):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://opencode.ai/zen/go/v1",
            reasoning_effort="none",
            protocol="openai_chat",
        )

        settings = provider._model_settings(0.2)

        assert settings["openai_reasoning_effort"] == "none"
        assert settings.get("extra_body") is None

    @pytest.mark.parametrize("protocol", [
        "openai_responses",
        "anthropic_messages",
        "gemini",
    ])
    def test_non_chat_protocols_drop_thinking_when_off(self, protocol):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            thinking=None,
            protocol=protocol,
        )

        settings = provider._model_settings(0.2)

        extra_body = settings.get("extra_body") or {}
        assert "thinking" not in extra_body

    @pytest.mark.parametrize("protocol", [
        "openai_responses",
        "anthropic_messages",
        "gemini",
    ])
    def test_non_chat_protocols_reject_enabled_thinking(self, protocol):
        with pytest.raises(ValueError, match="thinking is not supported"):
            PydanticAIProvider(
                api_key="test",
                base_url="https://example.test",
                thinking="enabled",
                protocol=protocol,
            )

    def test_responses_disables_server_storage(self):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            protocol="openai_responses",
        )

        settings = provider._model_settings(0.2)

        extra_body = settings.get("extra_body")
        assert isinstance(extra_body, dict)
        assert extra_body["store"] is False

    @pytest.mark.parametrize("protocol", [
        "openai_responses",
        "openai_chat",
        "anthropic_messages",
    ])
    def test_request_headers_are_protocol_independent(self, protocol):
        session = {"id": "save-a"}
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            protocol=protocol,
            request_headers=lambda: {
                "x-opencode-session": session["id"],
                "User-Agent": "LoreLoop/1.0",
            },
        )

        assert provider._model_settings(0.2)["extra_headers"] == {
            "x-opencode-session": "save-a",
            "User-Agent": "LoreLoop/1.0",
        }
        session["id"] = "save-b"
        assert provider._model_settings(0.2)["extra_headers"][
            "x-opencode-session"
        ] == "save-b"

    def test_responses_serializes_store_false(self):
        from openai import AsyncOpenAI
        from pydantic_ai.exceptions import ModelHTTPError
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.models import ModelRequestParameters
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        captured = {}

        def _capture(request):
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(
                400,
                json={"error": {"message": "request captured"}},
            )

        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://gateway.example.test/v1",
            protocol="openai_responses",
        )

        async def _request():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(_capture)
            ) as http_client:
                client = AsyncOpenAI(
                    api_key="test",
                    base_url="https://gateway.example.test/v1",
                    max_retries=0,
                    http_client=http_client,
                )
                model = OpenAIResponsesModel(
                    "test-model",
                    provider=OpenAIProvider(openai_client=client),
                )
                await model.request(
                    [ModelRequest.user_text_prompt("prompt")],
                    provider._model_settings(0.2),
                    ModelRequestParameters(),
                )

        with pytest.raises(ModelHTTPError):
            asyncio.run(_request())

        assert captured["url"] == "https://gateway.example.test/v1/responses"
        assert captured["body"]["store"] is False

    def test_generate_converts_text_and_usage(self):
        from pydantic_ai.models.test import TestModel

        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            protocol="openai_chat",
            model_instance=TestModel(custom_output_text='{"text": "hi"}'),
        )

        result = provider.generate("prompt", model="model", temperature=0.2)

        assert result.content == '{"text": "hi"}'
        assert isinstance(result.usage, dict)
        assert result.usage["output_tokens"] >= 1
        assert result.usage["input_tokens"] >= 1

    def test_generate_rejects_empty_text(self):
        from pydantic_ai.models.test import TestModel

        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            protocol="openai_chat",
            model_instance=TestModel(custom_output_text="   "),
        )

        with pytest.raises(LLMProviderError, match="empty response"):
            provider.generate("prompt", model="model", temperature=0.2)

    def test_generate_wraps_upstream_error(self):
        from pydantic_ai.models.function import FunctionModel

        def _boom(_messages, _info):
            raise RuntimeError("upstream 500")

        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            protocol="openai_chat",
            model_instance=FunctionModel(_boom),
        )

        with pytest.raises(LLMProviderError, match="Pydantic AI API call failed"):
            provider.generate("prompt", model="model", temperature=0.2)

    def test_stream_converts_increments_and_final_usage(self):
        from pydantic_ai.models.test import TestModel

        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            protocol="openai_chat",
            model_instance=TestModel(custom_output_text="hello world"),
        )

        chunks = list(
            provider.stream_generate("prompt", model="model", temperature=0.2)
        )

        joined = "".join(chunk.content for chunk in chunks)
        assert joined == "hello world"
        assert chunks[-1].usage is not None
        assert chunks[-1].usage["output_tokens"] >= 1

    def test_request_usage_normalizes_cache_fields(self):
        from pydantic_ai.usage import RequestUsage

        usage = _normalize_request_usage(RequestUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=80,
            cache_write_tokens=10,
        ))

        assert usage == {
            "input_tokens": 100,
            "prompt_tokens": 100,
            "output_tokens": 20,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cached_tokens": 80,
            "cache_creation_tokens": 10,
        }

    def test_request_usage_returns_none_when_empty(self):
        from pydantic_ai.usage import RequestUsage

        assert _normalize_request_usage(None) is None
        assert _normalize_request_usage(RequestUsage()) is None

    def test_models_are_cached_per_model_name(self, monkeypatch):
        from pydantic_ai.models.test import TestModel

        built: list[str] = []
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test",
            protocol="openai_chat",
        )

        def _spy(name):
            built.append(name)
            return TestModel(custom_output_text=f'["{name}"]')

        monkeypatch.setattr(provider, "_build_model", _spy)

        first = provider.generate("p", model="model-a", temperature=0.0)
        second = provider.generate("p", model="model-b", temperature=0.0)
        third = provider.generate("p", model="model-a", temperature=0.0)

        assert first.content == '["model-a"]'
        assert second.content == '["model-b"]'
        assert third.content == '["model-a"]'
        assert built == ["model-a", "model-b"]

    def test_stream_early_close_returns_promptly(self):
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        request_seen = threading.Event()
        stream_closed = threading.Event()
        captured = {}

        class _SlowSSE(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    b'data: {"id":"chunk-1","object":"chat.completion.chunk",'
                    b'"created":1,"model":"test-model","choices":[{"index":0,'
                    b'"delta":{"role":"assistant","content":"chunk0 "},'
                    b'"finish_reason":null}]}\n\n'
                )
                await asyncio.sleep(10)
                yield b"data: [DONE]\n\n"

            async def aclose(self):
                stream_closed.set()

        def _stream_response(request):
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode())
            request_seen.set()
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_SlowSSE(),
            )

        client = AsyncOpenAI(
            api_key="test",
            base_url="https://gateway.example.test/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(_stream_response)
            ),
        )
        model = OpenAIChatModel(
            "test-model",
            provider=OpenAIProvider(openai_client=client),
        )

        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://gateway.example.test/v1",
            protocol="openai_chat",
            thinking="enabled",
            model_instance=model,
        )

        gen = provider.stream_generate(
            "p", model="test-model", temperature=0.0
        )
        assert next(gen).content == "chunk0 "
        assert request_seen.is_set()
        start = time.perf_counter()
        gen.close()
        assert time.perf_counter() - start < 0.6
        assert stream_closed.wait(timeout=0.1)
        assert captured["url"] == (
            "https://gateway.example.test/v1/chat/completions"
        )
        assert captured["body"]["thinking"] == {"type": "enabled"}

    @pytest.mark.parametrize(
        ("protocol", "marker"),
        [
            ("openai_responses", "api.openai.com"),
            ("openai_chat", "api.openai.com"),
            ("anthropic_messages", "api.anthropic.com"),
        ],
    )
    def test_omitted_base_url_uses_sdk_official_default(
        self, protocol, marker, monkeypatch
    ):
        from app.core.llm.config import GatewayConfig

        # The project reads these variables into GatewayConfig. Remove them
        # here so this test exercises the SDK's own no-override default.
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

        config = GatewayConfig(
            api_key="test", base_url="", model="m", protocol=protocol
        )
        provider = PydanticAIProvider(
            api_key="test",
            base_url=config.base_url,
            protocol=protocol,
        )

        model = provider._build_model("m")

        assert marker in str(model._provider.base_url)

    def test_gemini_omitted_base_url_uses_sdk_default(self):
        from app.core.llm.config import GatewayConfig

        config = GatewayConfig(
            api_key="test", base_url="", model="m", protocol="gemini"
        )

        assert config.base_url == ""
        provider = PydanticAIProvider(
            api_key="test",
            base_url=config.base_url,
            protocol="gemini",
        )

        model = provider._build_model("m")

        assert "generativelanguage.googleapis.com" in str(
            model._provider.base_url
        )
        assert "openai" not in str(model._provider.base_url)


class TestFormalTimeoutBoundary:
    @staticmethod
    def _keep_alive_server():
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        calls: list[tuple[str, int]] = []

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                calls.append((self.path, self.client_address[1]))
                body = json.dumps({
                    "id": "response",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "m",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        return url, calls, server.shutdown, server.server_close

    @staticmethod
    def _stalling_server(delay: float = 5.0):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        calls: list = []

        class _StallHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.path)
                time.sleep(delay)
                try:
                    body = b"{}"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionError):
                    pass

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _StallHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        return url, calls, server.shutdown, server.server_close

    def test_formal_timeout_is_default(self):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test/v1",
            protocol="openai_chat",
        )

        assert provider.timeout_seconds == FORMAL_LLM_CALL_TIMEOUT_SECONDS
        assert (
            provider._model_settings(0.0)["timeout"]
            == FORMAL_LLM_CALL_TIMEOUT_SECONDS
        )

    def test_openai_clients_disable_hidden_retries(self):
        for protocol in ("openai_chat", "openai_responses"):
            provider = PydanticAIProvider(
                api_key="test",
                base_url="https://example.test/v1",
                protocol=protocol,
                timeout=0.2,
            )
            client = provider._build_model("m")._provider._client

            assert client.max_retries == 0

    def test_anthropic_client_disables_hidden_retries(self):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test/v1",
            protocol="anthropic_messages",
            timeout=0.2,
        )
        client = provider._build_model("m")._provider._client

        assert client.max_retries == 0

    @pytest.mark.parametrize("protocol", [
        "openai_responses",
        "openai_chat",
        "anthropic_messages",
        "gemini",
    ])
    def test_stalled_upstream_times_out_with_one_request(self, protocol):
        url, calls, shutdown, close = self._stalling_server()
        try:
            provider = PydanticAIProvider(
                api_key="test",
                base_url=url,
                protocol=protocol,
                # Budget covers cold first-request SDK setup (~0.5s) while
                # staying far below the 5s stall.
                timeout=1.0,
            )
            # Warm up SDK imports/model build outside the measured window so
            # the deadline strictly covers the in-flight request.
            provider._get_or_build_model("m")

            start = time.perf_counter()
            with pytest.raises(LLMTimeoutError) as excinfo:
                provider.generate("prompt", model="m", temperature=0.0)
            elapsed = time.perf_counter() - start
        finally:
            shutdown()
            close()

        assert len(calls) == 1
        assert excinfo.value.timeout_seconds == 1.0
        assert excinfo.value.protocol == protocol
        assert "1" in str(excinfo.value)
        assert elapsed < 5

    def test_stalled_stream_times_out_with_one_request(self):
        url, calls, shutdown, close = self._stalling_server()
        try:
            provider = PydanticAIProvider(
                api_key="test",
                base_url=url,
                protocol="openai_chat",
                timeout=1.0,
            )
            provider._get_or_build_model("m")

            start = time.perf_counter()
            with pytest.raises(LLMTimeoutError) as excinfo:
                list(provider.stream_generate("prompt", model="m", temperature=0.0))
            elapsed = time.perf_counter() - start
        finally:
            shutdown()
            close()

        assert len(calls) == 1
        assert excinfo.value.phase == "stream"
        assert elapsed < 5

    def test_stream_timeout_includes_entering_response_stream(self, monkeypatch):
        from contextlib import asynccontextmanager
        from pydantic_ai.models.test import TestModel

        @asynccontextmanager
        async def _delayed_stream(*_args, **_kwargs):
            await asyncio.sleep(0.15)
            yield SimpleNamespace(
                __aiter__=lambda self: self,
                __anext__=lambda self: (_ for _ in ()).throw(StopAsyncIteration),
                usage=lambda: None,
            )

        monkeypatch.setattr(
            "pydantic_ai.direct.model_request_stream", _delayed_stream
        )
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test/v1",
            protocol="openai_chat",
            timeout=0.03,
            model_instance=TestModel(),
        )

        start = time.perf_counter()
        with pytest.raises(LLMTimeoutError):
            list(provider.stream_generate("prompt", model="m", temperature=0.0))

        assert time.perf_counter() - start < 0.12

    @staticmethod
    def _dripping_server(payload_parts, delay=0.1):
        """HTTP server that drips response bytes forever-ish.

        Each part arrives faster than the client idle timeout, so only a
        total logical-call deadline can stop the call.
        """
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        calls: list = []

        class _DripHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for part in payload_parts:
                        time.sleep(delay)
                        self.wfile.write(part)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionError):
                    pass

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _DripHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        return url, calls, server.shutdown, server.server_close

    def test_steady_sync_drips_hit_total_timeout(self):
        finale = (
            b'{"id":"c","object":"chat.completion","created":1,"model":"m",'
            b'"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},'
            b'"finish_reason":"stop"}],"usage":{"prompt_tokens":1,'
            b'"completion_tokens":1,"total_tokens":2}}'
        )
        # Spaces keep the idle read alive; a valid body at the end means a
        # regressed implementation would succeed instead of timing out.
        url, calls, shutdown, close = self._dripping_server(
            [b" "] * 15 + [finale]
        )
        try:
            provider = PydanticAIProvider(
                api_key="test",
                base_url=url,
                protocol="openai_chat",
                timeout=0.5,
            )
            provider._get_or_build_model("m")

            start = time.perf_counter()
            with pytest.raises(LLMTimeoutError):
                provider.generate("prompt", model="m", temperature=0.0)
            elapsed = time.perf_counter() - start
        finally:
            shutdown()
            close()

        assert len(calls) == 1
        assert elapsed < 1.0

    def test_cached_client_survives_consecutive_keep_alive_requests(self):
        url, calls, shutdown, close = self._keep_alive_server()
        try:
            provider = PydanticAIProvider(
                api_key="test",
                base_url=url,
                protocol="openai_chat",
                timeout=1.0,
            )

            first = provider.generate("one", model="m", temperature=0.0)
            second = provider.generate("two", model="m", temperature=0.0)
        finally:
            shutdown()
            close()

        assert first.content == "ok"
        assert second.content == "ok"
        assert len(calls) == 2
        assert calls[0][1] == calls[1][1]

    def test_steady_stream_drips_hit_total_timeout(self):
        chunk = (
            b'data: {"id":"c","object":"chat.completion.chunk","created":1,'
            b'"model":"m","choices":[{"index":0,"delta":{"content":"x"},'
            b'"finish_reason":null}]}\n\n'
        )
        url, calls, shutdown, close = self._dripping_server([chunk] * 15)
        try:
            provider = PydanticAIProvider(
                api_key="test",
                base_url=url,
                protocol="openai_chat",
                timeout=0.5,
            )
            provider._get_or_build_model("m")

            start = time.perf_counter()
            with pytest.raises(LLMTimeoutError) as excinfo:
                list(provider.stream_generate("prompt", model="m", temperature=0.0))
            elapsed = time.perf_counter() - start
        finally:
            shutdown()
            close()

        assert len(calls) == 1
        assert excinfo.value.phase == "stream"
        assert elapsed < 1.0

    def test_connection_test_timeout_is_shorter_than_formal(self):
        assert CONNECTION_TEST_TIMEOUT_SECONDS < FORMAL_LLM_CALL_TIMEOUT_SECONDS

    @pytest.mark.parametrize("protocol", [
        "openai_responses",
        "openai_chat",
        "anthropic_messages",
        "gemini",
    ])
    def test_models_share_one_configured_client(self, protocol):
        provider = PydanticAIProvider(
            api_key="test",
            base_url="https://example.test/v1",
            protocol=protocol,
            timeout=0.5,
        )

        first = provider._get_or_build_model("model-a")
        second = provider._get_or_build_model("model-b")

        assert first is not second
        assert first._provider._client is second._provider._client


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
            "cached_tokens": None,
            "cache_creation_tokens": None,
            "reported": True,
        }
        assert normalize_token_usage({
            "input_tokens": 8,
            "output_tokens": 3,
        }) == {
            "input_tokens": 8,
            "output_tokens": 3,
            "total_tokens": None,
            "cached_tokens": None,
            "cache_creation_tokens": None,
            "reported": True,
        }

    def test_usage_extraction_openai_deepseek_cached_tokens(self):
        usage = _extract_usage(SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        ))
        assert usage == {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cached_tokens": 80,
        }

    def test_usage_extraction_anthropic_read_and_create(self):
        usage = _extract_usage(SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=10,
        ))
        assert usage == {
            "input_tokens": 100,
            "output_tokens": 20,
            "cached_tokens": 80,
            "cache_creation_tokens": 10,
        }

    def test_usage_extraction_gemini_cached_tokens(self):
        usage1 = _extract_usage(SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            usage_metadata=SimpleNamespace(cached_content_token_count=60),
        ))
        assert usage1 == {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "cached_tokens": 60,
        }

        usage2 = _extract_usage({
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "cached_content_token_count": 50,
        })
        assert usage2 == {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "cached_tokens": 50,
        }

    def test_usage_extraction_pydantic_model_extra(self):
        from pydantic import BaseModel, ConfigDict

        class _MockUsage(BaseModel):
            model_config = ConfigDict(extra="allow")
            prompt_tokens: int = 100
            completion_tokens: int = 20
            total_tokens: int = 120

        usage_obj = _MockUsage(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cached_content_token_count=75,
            cache_creation_input_tokens=15,
        )

        usage = _extract_usage(usage_obj)
        assert usage == {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cached_tokens": 75,
            "cache_creation_tokens": 15,
        }
