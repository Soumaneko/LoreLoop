from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from .schemas import (
    DirectorDecisionBatch,
    DirectorGuidance,
    DirectorPhase,
    DirectorSkillExecution,
    DirectorSkillExecutionBatch,
    DirectorSkillSpec,
)


def _guidance_key(guidance) -> str:
    if isinstance(guidance, DirectorGuidance):
        return json.dumps(
            {"name": guidance.name, "purpose": guidance.purpose},
            ensure_ascii=False,
            sort_keys=True,
        )
    return json.dumps(str(guidance), ensure_ascii=False, sort_keys=True)


def _guidance_text(guidance) -> str:
    """Forward guidance to handlers.

    Structured guidance forwards ``name: purpose`` and intentionally discards
    the ``description`` honeypot.
    """
    if isinstance(guidance, DirectorGuidance):
        return f"{guidance.name}: {guidance.purpose}"
    return str(guidance)


def _guidance_ok(guidance) -> bool:
    if isinstance(guidance, DirectorGuidance):
        return bool(
            guidance.name.strip()
            and guidance.purpose.strip()
            and guidance.description.strip()
        )
    return bool(str(guidance).strip())


DirectorSkillHandler = Callable[[BaseModel, str | None], BaseModel | dict | None]
DirectorArgumentsValidator = Callable[[BaseModel], None]


@dataclass(frozen=True)
class _RegisteredSkill:
    spec: DirectorSkillSpec
    arguments_model: type[BaseModel]
    handler: DirectorSkillHandler
    arguments_validator: DirectorArgumentsValidator | None


class DirectorSkillService:
    """Small registry that owns skill discovery, validation, and execution."""

    def __init__(self) -> None:
        self._skills: dict[str, _RegisteredSkill] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        phases: set[DirectorPhase],
        mutates: bool,
        arguments_model: type[BaseModel],
        handler: DirectorSkillHandler,
        arguments_validator: DirectorArgumentsValidator | None = None,
        max_requests_per_batch: int | None = None,
    ) -> None:
        if name in self._skills:
            raise ValueError(f"director skill already registered: {name}")
        if not phases:
            raise ValueError("director skill must support at least one phase")
        if mutates and "generation" in phases:
            raise ValueError(
                "mutating director skills cannot run during generation"
            )
        spec = DirectorSkillSpec(
            name=name,
            description=description,
            phases=sorted(phases),
            mutates=mutates,
            input_schema=arguments_model.model_json_schema(),
            max_requests_per_batch=max_requests_per_batch,
        )
        self._skills[name] = _RegisteredSkill(
            spec=spec,
            arguments_model=arguments_model,
            handler=handler,
            arguments_validator=arguments_validator,
        )

    def specs_for_phase(self, phase: DirectorPhase) -> list[DirectorSkillSpec]:
        return [
            registered.spec
            for registered in self._skills.values()
            if phase in registered.spec.phases
        ]

    def validate_batch(
        self,
        batch: DirectorDecisionBatch,
        *,
        phase: DirectorPhase,
    ) -> None:
        request_keys = [
            (
                request.skill,
                json.dumps(
                    request.arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                _guidance_key(request.guidance),
            )
            for request in batch.requests
        ]
        if len(request_keys) != len(set(request_keys)):
            raise ValueError("duplicate director skill request in one batch")
        request_counts: dict[str, int] = {}
        for request in batch.requests:
            registered = self._skills.get(request.skill)
            if registered is None:
                raise ValueError(f"unknown director skill: {request.skill}")
            if phase not in registered.spec.phases:
                raise ValueError(
                    f"director skill {request.skill} is unavailable in {phase}"
                )
            if not _guidance_ok(request.guidance):
                raise ValueError("director skills require guidance")
            request_counts[request.skill] = (
                request_counts.get(request.skill, 0) + 1
            )
            limit = registered.spec.max_requests_per_batch
            if limit is not None and request_counts[request.skill] > limit:
                raise ValueError(
                    f"director skill {request.skill} allows at most "
                    f"{limit} request(s) per batch"
                )
            arguments = registered.arguments_model.model_validate(
                request.arguments
            )
            if registered.arguments_validator is not None:
                registered.arguments_validator(arguments)

    def execute_batch(
        self,
        batch: DirectorDecisionBatch,
        *,
        phase: DirectorPhase,
        max_workers: int = 1,
    ) -> DirectorSkillExecutionBatch:
        self.validate_batch(batch, phase=phase)
        if max_workers < 1:
            raise ValueError("director skill max_workers must be >= 1")
        if len(batch.requests) < 2 or max_workers == 1:
            executions = [
                self._execute_request(request)
                for request in batch.requests
            ]
        else:
            with ThreadPoolExecutor(
                max_workers=min(max_workers, len(batch.requests)),
                thread_name_prefix="director-skill",
            ) as executor:
                executions = list(
                    executor.map(self._execute_request, batch.requests)
                )
        return DirectorSkillExecutionBatch(executions=executions)

    def _execute_request(self, request) -> DirectorSkillExecution:
        registered = self._skills[request.skill]
        arguments = registered.arguments_model.model_validate(
            request.arguments
        )
        raw_output = registered.handler(
            arguments,
            _guidance_text(request.guidance),
        )
        if raw_output is None:
            output = {}
        elif isinstance(raw_output, BaseModel):
            output = raw_output.model_dump(mode="json")
        elif isinstance(raw_output, dict):
            output = raw_output
        else:
            raise TypeError(
                "director skill handler must return BaseModel, dict, or None"
            )
        return DirectorSkillExecution(
            request_id=request.request_id,
            skill=request.skill,
            output=output,
            guidance=_guidance_text(request.guidance),
        )
