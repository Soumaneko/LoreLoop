from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Callable, Iterator
import asyncio
import concurrent.futures
import queue
import threading
from types import TracebackType
from typing import Any

from .errors import LLMProviderError, LLMTimeoutError
from .provider_classification import is_timeout_error


PROTOCOLS: tuple[str, ...] = (
    "openai_responses",
    "openai_chat",
    "anthropic_messages",
    "gemini",
)

#: Formal-run boundary for one logical LLM provider call (sync and stream).
FORMAL_LLM_CALL_TIMEOUT_SECONDS = 120.0

#: Connection tests use their own shorter boundary and never change formal runs.
CONNECTION_TEST_TIMEOUT_SECONDS = 30.0

#: Max time to wait for a cancelled stream task to release its HTTP stream.
_STREAM_STOP_TIMEOUT = 5.0


class _AsyncRequestRuntime:
    """Run all cached async SDK clients on one long-lived event loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(self, coroutine: Any) -> concurrent.futures.Future:
        with self._lock:
            if self._loop is None:
                threading.Thread(target=self._run, daemon=True).start()
                self._ready.wait()
            loop = self._loop
        if loop is None:  # pragma: no cover - _run always publishes it
            raise RuntimeError("LLM async runtime failed to start")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()


_ASYNC_REQUEST_RUNTIME = _AsyncRequestRuntime()


@dataclass
class ProviderResult:
    content: str
    usage: dict | None = None


class LLMProvider(ABC):
    @property
    def protocol_name(self) -> str:
        return "unknown"

    @property
    def timeout_seconds(self) -> float | None:
        return None

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
        reasoning_effort: str | None = None,
        timeout: float | None = FORMAL_LLM_CALL_TIMEOUT_SECONDS,
        protocol: str = "openai_chat",
        *,
        model_instance: Any | None = None,
        request_headers: Callable[[], dict[str, str]] | None = None,
        http_transport: Any | None = None,
    ):
        if protocol not in PROTOCOLS:
            raise ValueError(
                f"protocol must be one of {', '.join(PROTOCOLS)}"
            )
        if protocol != "openai_chat" and thinking == "enabled":
            raise ValueError(
                f"thinking is not supported for protocol '{protocol}' in this phase"
            )
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not timeout > 0
        ):
            raise ValueError("timeout must be a positive number of seconds")
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = default_model
        self._thinking = thinking
        self._reasoning_effort = reasoning_effort
        self._timeout = float(timeout) if timeout is not None else None
        self._protocol = protocol
        self._request_headers = request_headers
        # Test-only injection for stalled-transport tests. Formal runs leave
        # this unset and use the real vendor SDK clients below.
        self._http_transport = http_transport
        # Test-only injection: a ready-made Model used for every call,
        # bypassing the per-model-name cache below.
        self._model_instance = model_instance
        self._models: dict[str, Any] = {}
        # Vendor SDK clients are cached per (protocol, base URL): models only
        # differ by name, so every model of one provider instance shares one
        # configured client (timeout + no hidden retries).
        self._clients: dict[tuple[str, str], Any] = {}
        # Reentrant: model and client builders nest under this lock.
        self._model_lock = threading.RLock()

    @property
    def protocol_name(self) -> str:
        return self._protocol

    @property
    def timeout_seconds(self) -> float | None:
        return self._timeout

    def _timeout_error(self, *, model: str, phase: str) -> LLMTimeoutError:
        timeout = self._timeout
        seconds = (
            f"{timeout:g}" if isinstance(timeout, (int, float)) else "unknown"
        )
        return LLMTimeoutError(
            f"模型请求超过{seconds}秒，已停止等待。",
            timeout_seconds=timeout,
            model=model,
            protocol=self._protocol,
            phase=phase,
            transport_attempts=1,
        )

    def _wrap_call_error(
        self, exc: Exception, *, model: str, phase: str
    ) -> Exception:
        if isinstance(exc, LLMTimeoutError):
            if exc.timeout_seconds is None:
                exc.timeout_seconds = self._timeout
            if exc.model is None:
                exc.model = model
            if exc.protocol is None:
                exc.protocol = self._protocol
            if exc.phase is None:
                exc.phase = phase
            if exc.transport_attempts is None:
                exc.transport_attempts = 1
            return exc
        if is_timeout_error(exc):
            return self._timeout_error(model=model, phase=phase)
        return exc

    def _request_sync_with_total_timeout(
        self, pyd_model: Any, messages: Any, settings: Any
    ) -> Any:
        """Run one non-streamed request with a total logical-call deadline.

        Vendor SDK timeouts only bound idle reads, so a steadily dripping
        response could otherwise outlive the project boundary forever. The
        deadline is enforced with ``asyncio.wait_for`` on the shared SDK
        event loop: expiry cancels the in-flight model coroutine (closing the
        underlying HTTP request) instead of abandoning a still-running call.
        """
        from pydantic_ai.direct import model_request

        async def _run() -> Any:
            return await model_request(
                pyd_model, list(messages), model_settings=settings
            )

        try:
            coroutine = _run()
            if self._timeout is not None:
                coroutine = asyncio.wait_for(coroutine, timeout=self._timeout)
            return _ASYNC_REQUEST_RUNTIME.submit(coroutine).result()
        except asyncio.TimeoutError as e:
            raise TimeoutError("Provider call exceeded total timeout") from e

    def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        temperature: float = 0.2,
    ) -> ProviderResult:
        from .errors import LLMProviderError

        try:
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
            response = self._request_sync_with_total_timeout(
                pyd_model, messages, settings
            )
        except LLMProviderError as e:
            raise self._wrap_call_error(
                e, model=model or self._default_model, phase="generate"
            ) from e
        except Exception as e:
            wrapped = self._wrap_call_error(
                e, model=model or self._default_model, phase="generate"
            )
            if isinstance(wrapped, LLMTimeoutError):
                raise wrapped from e
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
            # and cannot be cancelled. _CancellableSyncStream keeps a handle
            # to the async task so early close/cancel stops promptly.
            with _CancellableSyncStream(
                pyd_model, messages, settings, self._timeout
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
        except LLMProviderError as e:
            raise self._wrap_call_error(
                e, model=model or self._default_model, phase="stream"
            ) from e
        except Exception as e:
            wrapped = self._wrap_call_error(
                e, model=model or self._default_model, phase="stream"
            )
            if isinstance(wrapped, LLMTimeoutError):
                raise wrapped from e
            raise LLMProviderError(
                f"Provider streaming call failed: {e}"
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

    def _test_http_client(self) -> Any | None:
        if self._http_transport is None:
            return None
        import httpx

        return httpx.AsyncClient(transport=self._http_transport)

    def _build_openai_client(self) -> Any:
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "base_url": self._base_url or None,
            "timeout": self._timeout,
            "max_retries": 0,
        }
        http_client = self._test_http_client()
        if http_client is not None:
            kwargs["http_client"] = http_client
        return AsyncOpenAI(**kwargs)

    def _build_anthropic_client(self) -> Any:
        from anthropic import AsyncAnthropic

        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "base_url": self._base_url or None,
            "timeout": self._timeout,
            "max_retries": 0,
        }
        http_client = self._test_http_client()
        if http_client is not None:
            kwargs["http_client"] = http_client
        return AsyncAnthropic(**kwargs)

    def _build_google_provider(self) -> Any:
        from google.genai import Client as GenAIClient
        from google.genai.types import HttpOptions, HttpRetryOptions
        from pydantic_ai.models import get_user_agent
        from pydantic_ai.providers.google import GoogleProvider

        import httpx

        if self._timeout is None:
            raise ValueError("Gemini calls require a positive timeout")
        if self._http_transport is not None:
            http_client = httpx.AsyncClient(transport=self._http_transport)
        else:
            http_client = httpx.AsyncClient(timeout=self._timeout)
        options = HttpOptions(
            base_url=self._base_url or None,
            headers={"User-Agent": get_user_agent()},
            timeout=int(1000 * self._timeout),
            retry_options=HttpRetryOptions(attempts=1),
            httpx_async_client=http_client,
        )
        return GoogleProvider(
            client=GenAIClient(
                vertexai=False,
                api_key=self._api_key,
                http_options=options,
            )
        )

    def _get_or_build_client(self) -> Any:
        key = (self._protocol, self._base_url or "")
        with self._model_lock:
            cached = self._clients.get(key)
            if cached is None:
                if self._protocol in ("openai_responses", "openai_chat"):
                    cached = self._build_openai_client()
                elif self._protocol == "anthropic_messages":
                    cached = self._build_anthropic_client()
                elif self._protocol == "gemini":
                    cached = self._build_google_provider()
                else:
                    raise ValueError(f"Unsupported protocol: {self._protocol}")
                self._clients[key] = cached
            return cached

    def _build_model(self, model_name: str) -> Any:
        protocol = self._protocol
        if protocol == "openai_responses":
            from pydantic_ai.models.openai import OpenAIResponsesModel
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                openai_client=self._get_or_build_client(),
            )
            return OpenAIResponsesModel(model_name, provider=provider)
        if protocol == "openai_chat":
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            provider = OpenAIProvider(
                openai_client=self._get_or_build_client(),
            )
            return OpenAIChatModel(model_name, provider=provider)
        if protocol == "anthropic_messages":
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider(
                anthropic_client=self._get_or_build_client(),
            )
            return AnthropicModel(model_name, provider=provider)
        if protocol == "gemini":
            from pydantic_ai.models.google import GoogleModel

            return GoogleModel(
                model_name, provider=self._get_or_build_client()
            )
        raise ValueError(f"Unsupported protocol: {protocol}")

    def _model_settings(self, temperature: float) -> Any:
        from pydantic_ai.settings import ModelSettings

        kwargs: dict[str, Any] = {"temperature": temperature}
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        if self._request_headers is not None:
            headers = self._request_headers()
            if headers:
                kwargs["extra_headers"] = headers
        extra_body: dict[str, Any] = {}
        if self._protocol == "openai_responses":
            # Explicitly disable server-side storage to keep the project
            # local, save-scoped privacy boundary.
            extra_body["store"] = False
        if self._protocol == "openai_chat" and self._thinking is not None:
            extra_body["thinking"] = {"type": self._thinking}
        if (
            self._protocol == "openai_chat"
            and self._reasoning_effort is not None
        ):
            kwargs["openai_reasoning_effort"] = self._reasoning_effort
        if extra_body:
            kwargs["extra_body"] = extra_body
        return ModelSettings(**kwargs)


class _CancellableSyncStream:
    """Sync wrapper around the async ``model_request_stream`` CM.

    Replaces ``model_request_stream_sync`` (whose ``__exit__`` joins the
    background thread until natural stream end with no way to cancel).
    The runner submits its consume task to the same long-lived loop used by
    non-streamed requests. Closing the consumer cancels that task, closes the
    HTTP stream via the ``async with`` exit, and returns promptly.
    """

    def __init__(
        self, model: Any, messages: Any, settings: Any, timeout: float | None
    ) -> None:
        self._model = model
        self._messages = messages
        self._settings = settings
        self._timeout = timeout
        self._queue: queue.Queue = queue.Queue()
        self._cancel_requested = threading.Event()
        self._stopped = threading.Event()
        self._exhausted = False
        self._future: concurrent.futures.Future | None = None
        self._final_usage: Any | None = None

    def __enter__(self) -> "_CancellableSyncStream":
        self._future = _ASYNC_REQUEST_RUNTIME.submit(self._consume())
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        if not self._exhausted:
            self._request_cancel()
            self._stopped.wait(timeout=_STREAM_STOP_TIMEOUT)

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
        if self._future is not None:
            self._future.cancel()

    async def _consume(self) -> None:
        try:
            request = self._consume_stream()
            if self._timeout is None:
                await request
            else:
                await asyncio.wait_for(request, timeout=self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._queue.put(exc)
        finally:
            self._queue.put(None)
            self._stopped.set()

    async def _consume_stream(self) -> None:
        from pydantic_ai.direct import model_request_stream

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
