import pytest

from types import SimpleNamespace
from app.core.llm.errors import LLMOutputValidationError
from app.core.llm.errors import LLMProviderError
from app.core.llm.mock import MockLLM
from app.core.llm.providers import MockProvider
from app.core.llm.providers import OpenAICompatibleProvider
from app.core.llm.providers import ProviderResult
from app.core.llm.providers import _extract_usage
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
