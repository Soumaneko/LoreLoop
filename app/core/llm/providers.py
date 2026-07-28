from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Iterator
import threading
from typing import Any


@dataclass
class ProviderResult:
    content: str
    usage: dict | None = None


class LLMProvider(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> ProviderResult:
        pass

    def stream_generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> Iterator[ProviderResult]:
        yield self.generate(prompt, model=model, temperature=temperature)


class MockProvider(LLMProvider):
    def __init__(self):
        self._responses: list[str] = []
        self._index = 0
        self._should_fail_provider = False
        self._should_timeout = False

    def set_responses(self, responses: list[str]) -> None:
        self._responses = responses
        self._index = 0

    def set_fail_provider(self, value: bool = True) -> None:
        self._should_fail_provider = value

    def set_timeout(self, value: bool = True) -> None:
        self._should_timeout = value

    def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        temperature: float = 0.2,
    ) -> ProviderResult:
        if self._should_timeout:
            raise TimeoutError("Mock provider timeout")

        if self._should_fail_provider:
            from .errors import LLMProviderError

            raise LLMProviderError("Mock provider error")

        if not self._responses:
            return ProviderResult(content='{"echo": "default"}')

        response = self._responses[self._index % len(self._responses)]
        self._index += 1
        return ProviderResult(content=response)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_model: str = "gpt-4o",
        thinking: str | None = None,
        timeout: float | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = default_model
        self._thinking = thinking
        self._timeout = timeout
        self._client_instance: Any | None = None
        self._client_lock = threading.Lock()

    @property
    def _client(self) -> Any:
        if self._client_instance is not None:
            return self._client_instance
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package is required for OpenAICompatibleProvider. "
                "Install it with: pip install openai"
            )

        with self._client_lock:
            if self._client_instance is None:
                kwargs = {
                    "api_key": self._api_key,
                    "base_url": self._base_url,
                }
                if self._timeout is not None:
                    kwargs["timeout"] = self._timeout
                self._client_instance = OpenAI(
                    **kwargs,
                )
        return self._client_instance

    def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        temperature: float = 0.2,
    ) -> ProviderResult:
        from .errors import LLMProviderError

        try:
            request: dict[str, Any] = {
                "model": model or self._default_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            }
            if self._thinking is not None:
                request["extra_body"] = {
                    "thinking": {"type": self._thinking},
                }
            response = self._client.chat.completions.create(
                **request,
            )
        except Exception as e:
            raise LLMProviderError(f"OpenAI-compatible API call failed: {e}") from e

        content = response.choices[0].message.content
        if content is None:
            raise LLMProviderError("Provider returned empty response")

        usage = _extract_usage(getattr(response, "usage", None))

        return ProviderResult(content=content, usage=usage)

    def stream_generate(
        self,
        prompt: str,
        *,
        model: str = "",
        temperature: float = 0.2,
    ) -> Iterator[ProviderResult]:
        from .errors import LLMProviderError

        response = None
        try:
            request: dict[str, Any] = {
                "model": model or self._default_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if self._thinking is not None:
                request["extra_body"] = {
                    "thinking": {"type": self._thinking},
                }
            response = self._client.chat.completions.create(**request)
            for chunk in response:
                usage = _extract_usage(getattr(chunk, "usage", None))
                content = ""
                if getattr(chunk, "choices", None):
                    content = chunk.choices[0].delta.content or ""
                if content or usage:
                    yield ProviderResult(content=content, usage=usage)
        except Exception as e:
            raise LLMProviderError(
                f"OpenAI-compatible streaming API call failed: {e}"
            ) from e
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _extract_usage(usage_obj: Any) -> dict | None:
    if not usage_obj:
        return None

    usage = {
        key: value
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        )
        if (value := getattr(usage_obj, key, None)) is not None
    }
    return usage or None
