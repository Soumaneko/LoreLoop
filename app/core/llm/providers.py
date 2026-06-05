from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> str:
        pass


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
    ) -> str:
        if self._should_timeout:
            raise TimeoutError("Mock provider timeout")

        if self._should_fail_provider:
            from .errors import LLMProviderError

            raise LLMProviderError("Mock provider error")

        if not self._responses:
            return '{"echo": "default"}'

        response = self._responses[self._index % len(self._responses)]
        self._index += 1
        return response


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_model: str = "gpt-4o",
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = default_model

    @property
    def _client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package is required for OpenAICompatibleProvider. "
                "Install it with: pip install openai"
            )

        return OpenAI(api_key=self._api_key, base_url=self._base_url)

    def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        temperature: float = 0.2,
    ) -> str:
        from .errors import LLMProviderError

        try:
            response = self._client.chat.completions.create(
                model=model or self._default_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
        except Exception as e:
            raise LLMProviderError(f"OpenAI-compatible API call failed: {e}") from e

        content = response.choices[0].message.content
        if content is None:
            raise LLMProviderError("Provider returned empty response")
        return content
