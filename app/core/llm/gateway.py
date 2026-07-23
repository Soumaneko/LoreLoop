import json
import hashlib
import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.diagnostics import DiagnosticSeverity

from .errors import (
    LLMError,
    LLMOutputParseError,
    LLMOutputValidationError,
    LLMProviderError,
    PromptRenderError,
)
from .prompts import PromptLoader
from .providers import LLMProvider, ProviderResult
from .tracing import TraceLogger


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider,
        prompt_loader: PromptLoader,
        tracer: TraceLogger | None = None,
        default_model: str = "gpt-4o",
    ):
        self._provider = provider
        self._prompt_loader = prompt_loader
        self._tracer = tracer or TraceLogger()
        self._default_model = default_model
        self._trace_context: dict[str, Any] = {}

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @property
    def prompt_loader(self) -> PromptLoader:
        return self._prompt_loader

    @property
    def tracer(self) -> TraceLogger:
        return self._tracer

    def set_trace_context(self, **kwargs: Any) -> None:
        self._trace_context.update(kwargs)

    def run(
        self,
        task: str,
        input_data: dict[str, Any],
        output_schema: type[BaseModel],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_retries: int = 2,
        trace_id: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
        on_attempt_start: Callable[[int], None] | None = None,
    ) -> BaseModel:
        prompt_template, prompt_version = self._prompt_loader.load(task)
        prompt_fingerprint = hashlib.sha256(
            prompt_template.encode("utf-8")
        ).hexdigest()
        model = model or self._default_model
        trace_id = trace_id or uuid.uuid4().hex

        base_prompt = self._build_prompt(prompt_template, input_data)
        current_prompt = base_prompt
        last_raw_output = ""
        last_error: str | None = None
        token_usage: dict | None = None

        for attempt in range(1, max_retries + 2):
            latency_ms = 0.0
            try:
                if on_attempt_start is not None:
                    on_attempt_start(attempt)
                if on_chunk is None:
                    raw_output, latency_ms, token_usage = self._call_provider(
                        current_prompt, model=model, temperature=temperature
                    )
                else:
                    raw_output, latency_ms, token_usage = self._call_provider_stream(
                        current_prompt,
                        model=model,
                        temperature=temperature,
                        on_chunk=on_chunk,
                    )
                last_raw_output = raw_output

                parsed = self._parse_json(raw_output)

                validated = self._validate_schema(parsed, output_schema)

                self._tracer.log(
                    trace_id=trace_id,
                    task=task,
                    model=model,
                    prompt_version=prompt_version,
                    input_data=input_data,
                    raw_output=raw_output,
                    parsed_output=parsed,
                    schema_valid=True,
                    latency_ms=latency_ms,
                    error=None,
                    attempt=attempt,
                    token_usage=token_usage,
                    turn_index=self._trace_context.get("turn_index"),
                    session_type=self._trace_context.get("session_type"),
                    prompt_fingerprint=prompt_fingerprint,
                )
                return validated

            except (LLMOutputParseError, LLMOutputValidationError) as e:
                last_error = str(e)
                self._tracer.log(
                    trace_id=trace_id,
                    task=task,
                    model=model,
                    prompt_version=prompt_version,
                    input_data=input_data,
                    raw_output=last_raw_output,
                    parsed_output=None,
                    schema_valid=False,
                    latency_ms=latency_ms,
                    error=last_error,
                    attempt=attempt,
                    token_usage=token_usage,
                    turn_index=self._trace_context.get("turn_index"),
                    session_type=self._trace_context.get("session_type"),
                    prompt_fingerprint=prompt_fingerprint,
                )
                self._log_diagnostic(
                    severity="warning" if attempt <= max_retries else "error",
                    code=(
                        "llm_output_parse_failed"
                        if isinstance(e, LLMOutputParseError)
                        else "llm_output_validation_failed"
                    ),
                    message=last_error,
                    trace_id=trace_id,
                    task=task,
                    attempt=attempt,
                )
                if attempt <= max_retries:
                    current_prompt = self._build_retry_prompt(
                        base_prompt, last_raw_output, last_error
                    )
                    continue
                raise

            except LLMProviderError as e:
                last_error = str(e)
                self._tracer.log(
                    trace_id=trace_id,
                    task=task,
                    model=model,
                    prompt_version=prompt_version,
                    input_data=input_data,
                    raw_output="",
                    parsed_output=None,
                    schema_valid=False,
                    latency_ms=0,
                    error=last_error,
                    attempt=attempt,
                    token_usage=None,
                    turn_index=self._trace_context.get("turn_index"),
                    session_type=self._trace_context.get("session_type"),
                    prompt_fingerprint=prompt_fingerprint,
                )
                self._log_diagnostic(
                    severity="warning" if attempt <= max_retries else "error",
                    code="llm_provider_failed",
                    message=last_error,
                    trace_id=trace_id,
                    task=task,
                    attempt=attempt,
                )
                if attempt <= max_retries:
                    continue
                raise

            except Exception as e:
                last_error = str(e)
                self._tracer.log(
                    trace_id=trace_id,
                    task=task,
                    model=model,
                    prompt_version=prompt_version,
                    input_data=input_data,
                    raw_output="",
                    parsed_output=None,
                    schema_valid=False,
                    latency_ms=0,
                    error=last_error,
                    attempt=attempt,
                    token_usage=None,
                    turn_index=self._trace_context.get("turn_index"),
                    session_type=self._trace_context.get("session_type"),
                    prompt_fingerprint=prompt_fingerprint,
                )
                self._log_diagnostic(
                    severity="error",
                    code="llm_unexpected_error",
                    message=last_error,
                    trace_id=trace_id,
                    task=task,
                    attempt=attempt,
                )
                raise

        raise RuntimeError("unreachable LLM retry state")

    def _log_diagnostic(
        self,
        *,
        severity: DiagnosticSeverity,
        code: str,
        message: str,
        trace_id: str,
        task: str,
        attempt: int,
    ) -> None:
        if severity == "warning":
            log = self._tracer.diagnostics.warning
        elif severity == "error":
            log = self._tracer.diagnostics.error
        else:
            raise ValueError(f"Unsupported diagnostic severity: {severity}")
        log(
            source="llm_gateway",
            code=code,
            message=message,
            trace_id=trace_id,
            task=task,
            attempt=attempt,
            turn_index=self._trace_context.get("turn_index"),
            session_type=self._trace_context.get("session_type"),
        )

    def _build_prompt(
        self, template: str, input_data: dict[str, Any]
    ) -> str:
        try:
            return template.format(**input_data)
        except KeyError as exc:
            missing = exc.args[0]
            raise PromptRenderError(
                f"Missing prompt template variable: {missing}"
            ) from exc
        except (IndexError, ValueError) as exc:
            raise PromptRenderError(f"Invalid prompt template: {exc}") from exc

    def _build_retry_prompt(
        self,
        base_prompt: str,
        raw_output: str,
        error: str,
    ) -> str:
        return (
            f"{base_prompt}\n\n"
            f"---\n\n"
            f"Your previous response was invalid:\n\n"
            f"```\n{raw_output}\n```\n\n"
            f"Error: {error}\n\n"
            f"Please correct the issue and respond with valid JSON only."
        )

    def _call_provider(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
    ) -> tuple[str, float, dict | None]:
        start = time.time()
        try:
            result = self._provider.generate(
                prompt,
                model=model,
                temperature=temperature,
            )
        except LLMError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Provider call failed: {e}") from e
        latency = (time.time() - start) * 1000
        return result.content, latency, result.usage

    def _call_provider_stream(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        on_chunk: Callable[[str], None],
    ) -> tuple[str, float, dict | None]:
        start = time.time()
        parts: list[str] = []
        token_usage: dict | None = None
        try:
            for result in self._provider.stream_generate(
                prompt,
                model=model,
                temperature=temperature,
            ):
                if result.content:
                    parts.append(result.content)
                    on_chunk(result.content)
                if result.usage is not None:
                    token_usage = result.usage
        except LLMError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Provider streaming call failed: {e}") from e
        latency = (time.time() - start) * 1000
        return "".join(parts), latency, token_usage

    def _parse_json(self, raw_output: str) -> Any:
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if len(lines) > 1:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMOutputParseError(
                f"Failed to parse LLM output as JSON: {e}\nOutput: {raw_output[:500]}"
            ) from e

    def _validate_schema(
        self, parsed: Any, output_schema: type[BaseModel]
    ) -> BaseModel:
        try:
            return output_schema.model_validate(parsed)
        except ValidationError as e:
            raise LLMOutputValidationError(
                f"Schema validation failed for {output_schema.__name__}: {e}"
            ) from e
