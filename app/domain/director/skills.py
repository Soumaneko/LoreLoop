from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from .schemas import (
    DirectorDecisionBatch,
    DirectorPhase,
    DirectorSkillExecution,
    DirectorSkillExecutionBatch,
    DirectorSkillSpec,
)


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
                request.guidance or "",
            )
            for request in batch.requests
        ]
        if len(request_keys) != len(set(request_keys)):
            raise ValueError("duplicate director skill request in one batch")
        for request in batch.requests:
            registered = self._skills.get(request.skill)
            if registered is None:
                raise ValueError(f"unknown director skill: {request.skill}")
            if phase not in registered.spec.phases:
                raise ValueError(
                    f"director skill {request.skill} is unavailable in {phase}"
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
    ) -> DirectorSkillExecutionBatch:
        self.validate_batch(batch, phase=phase)
        executions: list[DirectorSkillExecution] = []
        for request in batch.requests:
            registered = self._skills[request.skill]
            arguments = registered.arguments_model.model_validate(
                request.arguments
            )
            raw_output = registered.handler(
                arguments,
                request.guidance,
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
            executions.append(
                DirectorSkillExecution(
                    request_id=request.request_id,
                    skill=request.skill,
                    output=output,
                    guidance=request.guidance,
                )
            )
        return DirectorSkillExecutionBatch(executions=executions)
