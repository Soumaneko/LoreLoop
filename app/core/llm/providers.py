from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Iterator
import asyncio
import queue
import threading
from types import TracebackType
from typing import Any


PROTOCOLS: tuple[str, ...] = (
    "openai_responses",
    "openai_chat",
    "anthropic_messages",
    "gemini",
)

#: Max time to wait for a cancelled stream's background thread to stop.
_STREAM_STOP_TIMEOUT = 5.0


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


class PydanticAIProvider(LLMProvider):
    """Single project adapter over Pydantic AI Direct Model API.

    One class serves all four wire protocols; operators are presets
    (Issue 02), never new provider classes. Only ``generate`` and
    ``stream_generate`` translate Pydantic AI responses into the existing
    ``ProviderResult``; prompt rendering, JSON parsing, schema validation,
    salvage/retry, trace/diagnostics and cancel/degrade stay in
    ``LLMGateway``.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_model: str = "gpt-4o",
        thinking: str | None = None,
        timeout: float | None = None,
        protocol: str = "openai_chat",
        *,
        model_instance: Any | None = None,
    ):
        if protocol not in PROTOCOLS:
            raise ValueError(
                f"protocol must be one of {', '.join(PROTOCOLS)}"
            )
        if protocol != "openai_chat" and thinking == "enabled":
            raise ValueError(
                f"thinking is not supported for protocol '{protocol}' in this phase"
            )
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = default_model
        self._thinking = thinking
        self._timeout = timeout
        self._protocol = protocol
        # Test-only injection: a ready-made Model used for every call,
        # bypassing the per-model-name cache below.
        self._model_instance = model_instance
        self._models: dict[str, Any] = {}
        self._model_lock = threading.Lock()

    def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        temperature: float = 0.2,
    ) -> ProviderResult:
        from .errors import LLMProviderError

        try:
            from pydantic_ai.direct import model_request_sync
            from pydantic_ai.messages import ModelRequest
        except ImportError as e:
            raise LLMProviderError(
                "pydantic-ai-slim is required for PydanticAIProvider. "
                'Install it with: pip install "pydantic-ai-slim[openai,anthropic,google]"'
            ) from e

        try:
            pyd_model = self._get_or_build_model(model or self._default_model)
            settings = self._model_settings(temperature)
            messages = [ModelRequest.user_text_prompt(prompt)]
            response = model_request_sync(
                pyd_model, messages, model_settings=settings
            )
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Pydantic AI API call failed: {e}") from e

        content = response.text or ""
        if not content.strip():
            raise LLMProviderError("Provider returned empty response")

        usage = _normalize_request_usage(getattr(response, "usage", None))
        return ProviderResult(content=content, usage=usage)

    def stream_generate(
        self,
        prompt: str,
        *,
        model: str = "",
        temperature: float = 0.2,
    ) -> Iterator[ProviderResult]:
        from .errors import LLMProviderError

        try:
            from pydantic_ai.messages import (
                ModelRequest,
                PartDeltaEvent,
                PartStartEvent,
                TextPart,
                TextPartDelta,
            )
        except ImportError as e:
            raise LLMProviderError(
                "pydantic-ai-slim is required for PydanticAIProvider. "
                'Install it with: pip install "pydantic-ai-slim[openai,anthropic,google]"'
            ) from e

        try:
            pyd_model = self._get_or_build_model(model or self._default_model)
            settings = self._model_settings(temperature)
            messages = [ModelRequest.user_text_prompt(prompt)]
            # NOTE: deliberately not model_request_stream_sync — its
            # __exit__ joins the background thread until natural stream end
            # and cannot be cancelled. _CancellableSyncStream owns its loop
            # so early close/cancel stops promptly (see §0 repro).
            with _CancellableSyncStream(
                pyd_model, messages, settings
            ) as runner:
                for event in runner:
                    content = ""
                    if isinstance(event, PartDeltaEvent) and isinstance(
                        event.delta, TextPartDelta
                    ):
                        content = event.delta.content_delta or ""
                    elif isinstance(event, PartStartEvent) and isinstance(
                        event.part, TextPart
                    ):
                        content = event.part.content or ""
                    if content:
                        yield ProviderResult(content=content, usage=None)
                usage = _normalize_request_usage(runner.final_usage())
                if usage is not None:
                    yield ProviderResult(content="", usage=usage)
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(
                f"Pydantic AI streaming API call failed: {e}"
            ) from e

    def _get_or_build_model(self, model_name: str) -> Any:
        if self._model_instance is not None:
            return self._model_instance
        with self._model_lock:
            cached = self._models.get(model_name)
            if cached is None:
                cached = self._build_model(model_name)
                self._models[model_name] = cached
            return cached

    def _build_model(self, model_name: str) -> Any:
        protocol = self._protocol
        if protocol == "openai_responses":
            from pydantic_ai.models.openai import OpenAIResponsesModel
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                base_url=self._base_url or None,
                api_key=self._api_key,
            )
            return OpenAIResponsesModel(model_name, provider=provider)
        if protocol == "openai_chat":
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                base_url=self._base_url or None,
                api_key=self._api_key,
            )
            return OpenAIChatModel(model_name, provider=provider)
        if protocol == "anthropic_messages":
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider(
                api_key=self._api_key,
                base_url=self._base_url or None,
            )
            return AnthropicModel(model_name, provider=provider)
        if protocol == "gemini":
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider

            provider = GoogleProvider(
                api_key=self._api_key,
                base_url=self._base_url or None,
            )
            return GoogleModel(model_name, provider=provider)
        raise ValueError(f"Unsupported protocol: {protocol}")

    def _model_settings(self, temperature: float) -> Any:
        from pydantic_ai.settings import ModelSettings

        kwargs: dict[str, Any] = {"temperature": temperature}
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        extra_body: dict[str, Any] = {}
        if self._protocol == "openai_responses":
            # Explicitly disable server-side storage to keep the project
            # local, save-scoped privacy boundary.
            extra_body["store"] = False
        if self._protocol == "openai_chat" and self._thinking == "enabled":
            extra_body["thinking"] = {"type": "enabled"}
        if extra_body:
            kwargs["extra_body"] = extra_body
        return ModelSettings(**kwargs)


class _CancellableSyncStream:
    """Sync wrapper around the async ``model_request_stream`` CM.

    Replaces ``model_request_stream_sync`` (whose ``__exit__`` joins the
    background thread until natural stream end with no way to cancel).
    This runner owns its event loop and consume task, so closing the
    consumer early cancels the task, closes the HTTP stream via the
    ``async with`` exit, and returns promptly instead of waiting out the
    remaining response.
    """

    def __init__(self, model: Any, messages: Any, settings: Any) -> None:
        self._model = model
        self._messages = messages
        self._settings = settings
        self._queue: queue.Queue = queue.Queue()
        self._cancel_requested = threading.Event()
        self._exhausted = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None
        self._final_usage: Any | None = None

    def __enter__(self) -> "_CancellableSyncStream":
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        if not self._exhausted:
            self._request_cancel()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=_STREAM_STOP_TIMEOUT)

    def __iter__(self) -> Iterator[Any]:
        try:
            while True:
                item = self._queue.get()
                if item is None:  # end of stream
                    self._exhausted = True
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if not self._exhausted:
                self._request_cancel()

    def final_usage(self) -> Any | None:
        """Usage snapshot captured on natural exhaustion, else None."""
        return self._final_usage

    def _request_cancel(self) -> None:
        self._cancel_requested.set()
        loop, task = self._loop, self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # loop already closed

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._task = loop.create_task(self._consume())
            try:
                loop.run_until_complete(self._task)
            except asyncio.CancelledError:
                pass
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    async def _consume(self) -> None:
        from pydantic_ai.direct import model_request_stream

        try:
            async with model_request_stream(
                self._model,
                self._messages,
                model_settings=self._settings,
            ) as stream:
                async for event in stream:
                    if self._cancel_requested.is_set():
                        break
                    self._queue.put(event)
                else:
                    self._final_usage = stream.usage()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._queue.put(exc)
        finally:
            self._queue.put(None)


def _normalize_request_usage(usage_obj: Any) -> dict | None:
    """Map Pydantic AI ``RequestUsage`` to the project's usage dict."""
    if usage_obj is None:
        return None

    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    input_tokens = _get(usage_obj, "input_tokens")
    output_tokens = _get(usage_obj, "output_tokens")
    cache_read = _get(usage_obj, "cache_read_tokens")
    cache_write = _get(usage_obj, "cache_write_tokens")

    if not (input_tokens or output_tokens or cache_read or cache_write):
        return None

    total = None
    if input_tokens is not None or output_tokens is not None:
        total = (input_tokens or 0) + (output_tokens or 0)

    usage: dict[str, Any] = {}
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
        usage["prompt_tokens"] = input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
        usage["completion_tokens"] = output_tokens
    if total is not None:
        usage["total_tokens"] = total
    if cache_read:
        usage["cached_tokens"] = cache_read
    if cache_write:
        usage["cache_creation_tokens"] = cache_write

    return usage or None


def _extract_usage(usage_obj: Any) -> dict | None:
    if not usage_obj:
        return None

    if hasattr(usage_obj, "model_dump") and callable(usage_obj.model_dump):
        try:
            usage_obj = usage_obj.model_dump()
        except Exception:
            pass

    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        val = getattr(obj, key, None)
        if val is None:
            extra = getattr(obj, "model_extra", None)
            if isinstance(extra, dict):
                return extra.get(key)
        return val

    usage: dict[str, Any] = {}

    for key in (
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ):
        if (value := _get(usage_obj, key)) is not None:
            usage[key] = value

    # Extract cached_tokens
    # 1. OpenAI / DeepSeek: prompt_tokens_details.cached_tokens
    details = _get(usage_obj, "prompt_tokens_details")
    cached = _get(details, "cached_tokens") if details is not None else None
    # 2. Anthropic: cache_read_input_tokens
    if cached is None:
        cached = _get(usage_obj, "cache_read_input_tokens")
    # 3. Pydantic AI RequestUsage: cache_read_tokens
    if cached is None:
        cached = _get(usage_obj, "cache_read_tokens")
    # 4. Gemini / direct: cached_content_token_count or usage_metadata.cached_content_token_count
    if cached is None:
        cached = _get(usage_obj, "cached_content_token_count")
    if cached is None:
        meta = _get(usage_obj, "usage_metadata")
        cached = _get(meta, "cached_content_token_count") if meta is not None else None
    # 5. Direct cached_tokens fallback
    if cached is None:
        cached = _get(usage_obj, "cached_tokens")

    if cached is not None:
        usage["cached_tokens"] = cached

    # Extract cache_creation_tokens
    # Anthropic: cache_creation_input_tokens or cache_creation_tokens
    creation = _get(usage_obj, "cache_creation_input_tokens")
    if creation is None:
        creation = _get(usage_obj, "cache_creation_tokens")
    # Pydantic AI RequestUsage: cache_write_tokens
    if creation is None:
        creation = _get(usage_obj, "cache_write_tokens")

    if creation is not None:
        usage["cache_creation_tokens"] = creation

    return usage or None
