from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
    LLMProviderError,
)
from app.core.llm.schema import schema_json

from .schemas import (
    DirectorDecisionBatch,
    DirectorDecisionBatchLoose,
    DirectorObservation,
    DirectorSkillRequest,
)
from .skills import DirectorSkillService

_FALLBACK_REASONING = "Director decision fallback: no usable skill requests."


@dataclass(frozen=True)
class DirectorWarning:
    code: str
    message: str


@dataclass
class _AttemptRecord:
    reasoning: str = _FALLBACK_REASONING
    requests: list[DirectorSkillRequest] = field(default_factory=list)
    warnings: list[DirectorWarning] = field(default_factory=list)


class DirectorAgent:
    """Chooses a bounded batch from the skills available in the current phase.

    The same per-request salvage strategy applies to generation and confirmation
    phases. After the single feedback retry, valid requests are preserved;
    invalid/conflicting requests are dropped. If nothing usable remains, an
    empty decision batch is returned with a fallback warning.
    """

    def __init__(
        self,
        llm: LLMGateway,
        skills: DirectorSkillService,
    ) -> None:
        self._llm = llm
        self._skills = skills
        self.last_warnings: list[DirectorWarning] = []

    def decide_generation(
        self,
        observation: DirectorObservation,
        *,
        trace_id: str | None = None,
    ) -> DirectorDecisionBatch:
        return self._decide(
            observation,
            task="director_agent_decide_generation",
            trace_id=trace_id,
        )

    def decide_confirmation(
        self,
        observation: DirectorObservation,
        *,
        trace_id: str | None = None,
    ) -> DirectorDecisionBatch:
        return self._decide(
            observation,
            task="director_agent_decide_confirmation",
            trace_id=trace_id,
        )

    def _decide(
        self,
        observation: DirectorObservation,
        *,
        task: str,
        trace_id: str | None = None,
    ) -> DirectorDecisionBatch:
        self.last_warnings = []
        available_skills = self._skills.specs_for_phase(observation.phase)
        if not available_skills:
            return DirectorDecisionBatch(
                reasoning="当前阶段没有可用的导演技能。",
            )

        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: DirectorDecisionBatchLoose) -> None:
            reasoning = (
                output.reasoning.strip()
                if output.reasoning and output.reasoning.strip()
                else _FALLBACK_REASONING
            )
            if len(reasoning) > 500:
                reasoning = _FALLBACK_REASONING
            top_warnings: list[DirectorWarning] = []
            extra_keys = sorted((output.model_extra or {}).keys())
            if extra_keys:
                top_warnings.append(DirectorWarning(
                    code="director_top_level",
                    message=f"unexpected top-level fields: {extra_keys}",
                ))

            requests: list[DirectorSkillRequest] = []
            dropped_warnings: list[DirectorWarning] = []
            retry_errors = [warning.message for warning in top_warnings]
            for index, raw in enumerate(output.requests):
                try:
                    request = _parse_request(raw)
                except ValidationError as exc:
                    retry_errors.append(f"request[{index}]: {exc}")
                    dropped_warnings.append(DirectorWarning(
                        code="director_request_dropped",
                        message=f"request[{index}] dropped: {exc}",
                    ))
                    continue
                candidate = [*requests, request]
                if not _batch_valid(self._skills, candidate, observation.phase):
                    retry_errors.append(
                        f"request[{index}] conflicts with earlier requests"
                    )
                    dropped_warnings.append(DirectorWarning(
                        code="director_request_dropped",
                        message=(
                            f"request[{index}] dropped: conflicts with "
                            "earlier requests"
                        ),
                    ))
                    continue
                requests = candidate

            attempt_records[current_attempt] = _AttemptRecord(
                reasoning=reasoning,
                requests=requests,
                warnings=[*top_warnings, *dropped_warnings],
            )
            if retry_errors:
                raise ValueError("; ".join(retry_errors))

        def salvage(exception: Exception) -> DirectorDecisionBatch:
            latest = attempt_records.get(2)
            if latest is None:
                latest = attempt_records.get(1)
            usable = attempt_records.get(2)
            if usable is None or not usable.requests:
                usable = attempt_records.get(1)
            if usable is not None and usable.requests:
                self.last_warnings = list(usable.warnings)
                return DirectorDecisionBatch(
                    reasoning=usable.reasoning,
                    requests=usable.requests,
                )
            warnings = list(latest.warnings) if latest is not None else []
            warnings.append(DirectorWarning(
                code="director_decision_fallback",
                message=(
                    "Director decision failed; no skill requests were selected: "
                    f"{exception}"
                ),
            ))
            self.last_warnings = warnings
            return DirectorDecisionBatch(
                reasoning=_FALLBACK_REASONING,
                requests=[],
            )

        try:
            result = cast(
                DirectorDecisionBatchLoose,
                self._llm.run(
                    task=task,
                    input_data={
                        "director_phase": observation.phase,
                        "director_observation_json": observation.model_dump_json(
                            indent=2,
                            exclude={"recent_director_log"},
                        ),
                        "recent_director_log_json": json.dumps(
                            observation.recent_director_log,
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "available_skills_json": json.dumps(
                            [
                                skill.model_dump(mode="json")
                                for skill in available_skills
                            ],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "output_schema_json": schema_json(
                            DirectorDecisionBatch
                        ),
                    },
                    output_schema=DirectorDecisionBatchLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    temperature=0.4,
                    trace_id=trace_id,
                ),
            )
            if current_attempt in attempt_records:
                record = attempt_records[current_attempt]
            else:
                # StubGateway compatibility: real Gateway always calls
                # output_validator, but test stubs return without it.
                requests: list[DirectorSkillRequest] = []
                warnings: list[DirectorWarning] = []
                for index, raw in enumerate(result.requests):
                    try:
                        request = _parse_request(raw)
                    except ValidationError as exc:
                        warnings.append(DirectorWarning(
                            code="director_request_dropped",
                            message=f"request[{index}] dropped: {exc}",
                        ))
                        continue
                    if not _batch_valid(
                        self._skills,
                        [*requests, request],
                        observation.phase,
                    ):
                        warnings.append(DirectorWarning(
                            code="director_request_dropped",
                            message=f"request[{index}] dropped: conflict",
                        ))
                        continue
                    requests.append(request)
                reasoning = (
                    result.reasoning.strip()
                    if result.reasoning and result.reasoning.strip()
                    else _FALLBACK_REASONING
                )
                if len(reasoning) > 500:
                    reasoning = _FALLBACK_REASONING
                record = _AttemptRecord(
                    reasoning=reasoning,
                    requests=requests,
                    warnings=warnings,
                )
            self.last_warnings = list(record.warnings)
            return DirectorDecisionBatch(
                reasoning=record.reasoning,
                requests=record.requests,
            )
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
            LLMProviderError,
            ValueError,
        ) as exc:
            return salvage(exc)


def _parse_request(raw: Any) -> DirectorSkillRequest:
    if isinstance(raw, dict):
        raw = dict(raw)
        for key in ("request_id", "skill"):
            if isinstance(raw.get(key), str):
                raw[key] = raw[key].strip()
        guidance = raw.get("guidance")
        if isinstance(guidance, str):
            raw["guidance"] = guidance.strip()
    return DirectorSkillRequest.model_validate(raw)


def _batch_valid(
    service: DirectorSkillService,
    requests: list[DirectorSkillRequest],
    phase: str,
) -> bool:
    try:
        service.validate_batch(
            DirectorDecisionBatch(
                reasoning=_FALLBACK_REASONING,
                requests=requests,
            ),
            phase=phase,
        )
        return True
    except (ValueError, ValidationError):
        return False
