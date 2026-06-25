import json
import time
import uuid
from typing import Any

from pydantic import BaseModel, ValidationError

from .errors import (
    LLMError,
    LLMOutputParseError,
    LLMOutputValidationError,
    LLMProviderError,
)
from .prompts import PromptLoader
from .providers import LLMProvider
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

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @property
    def prompt_loader(self) -> PromptLoader:
        return self._prompt_loader

    @property
    def tracer(self) -> TraceLogger:
        return self._tracer

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
    ) -> BaseModel:
        prompt_template, prompt_version = self._prompt_loader.load(task)
        model = model or self._default_model
        trace_id = trace_id or uuid.uuid4().hex

        current_prompt = self._build_prompt(prompt_template, input_data)
        last_raw_output = ""
        last_error: str | None = None
        total_start = time.time()

        for attempt in range(max_retries + 1):
            try:
                raw_output, latency_ms = self._call_provider(
                    current_prompt, model=model, temperature=temperature
                )
                last_raw_output = raw_output

                parsed = self._parse_json(raw_output)

                validated = self._validate_schema(parsed, output_schema)

                total_latency = (time.time() - total_start) * 1000
                self._tracer.log(
                    trace_id=trace_id,
                    task=task,
                    model=model,
                    prompt_version=prompt_version,
                    input_data=input_data,
                    raw_output=raw_output,
                    parsed_output=parsed,
                    schema_valid=True,
                    latency_ms=total_latency,
                    error=None,
                )
                return validated

            except (LLMOutputParseError, LLMOutputValidationError) as e:
                last_error = str(e)
                if attempt < max_retries:
                    current_prompt = self._build_retry_prompt(
                        prompt_template, input_data, last_raw_output, last_error
                    )
                    continue
                self._log_failure(
                    trace_id=trace_id,
                    task=task,
                    model=model,
                    prompt_version=prompt_version,
                    input_data=input_data,
                    raw_output=last_raw_output,
                    total_start=total_start,
                    error=last_error,
                )
                raise
            except LLMProviderError as e:
                last_error = str(e)
                if attempt < max_retries:
                    continue
                self._log_failure(
                    trace_id=trace_id,
                    task=task,
                    model=model,
                    prompt_version=prompt_version,
                    input_data=input_data,
                    raw_output=last_raw_output,
                    total_start=total_start,
                    error=last_error,
                )
                raise
            except Exception as e:
                last_error = str(e)
                self._log_failure(
                    trace_id=trace_id,
                    task=task,
                    model=model,
                    prompt_version=prompt_version,
                    input_data=input_data,
                    raw_output=last_raw_output,
                    total_start=total_start,
                    error=last_error,
                )
                raise

        raise RuntimeError("unreachable LLM retry state")

    def _log_failure(
        self,
        *,
        trace_id: str,
        task: str,
        model: str,
        prompt_version: str,
        input_data: dict[str, Any],
        raw_output: str,
        total_start: float,
        error: str,
    ) -> None:
        self._tracer.log(
            trace_id=trace_id,
            task=task,
            model=model,
            prompt_version=prompt_version,
            input_data=input_data,
            raw_output=raw_output,
            parsed_output=None,
            schema_valid=False,
            latency_ms=(time.time() - total_start) * 1000,
            error=error,
        )

    def _build_prompt(
        self, template: str, input_data: dict[str, Any]
    ) -> str:
        return template.format(**input_data)

    def _build_retry_prompt(
        self,
        template: str,
        input_data: dict[str, Any],
        raw_output: str,
        error: str,
    ) -> str:
        base = template.format(**input_data)
        return (
            f"{base}\n\n"
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
    ) -> tuple[str, float]:
        start = time.time()
        try:
            raw_output = self._provider.generate(
                prompt,
                model=model,
                temperature=temperature,
            )
        except LLMError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Provider call failed: {e}") from e
        latency = (time.time() - start) * 1000
        return raw_output, latency

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
