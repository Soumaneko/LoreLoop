import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from app.core.llm.errors import LLMOutputValidationError
from app.core.llm.errors import LLMProviderError
from app.core.llm.mock import MockLLM
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
            ("disabled", None),
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
