from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import display_schema_json

from .schemas import (
    DIRECTOR_REASONING_ACCEPT_LIMIT,
    DIRECTOR_REASONING_HARD_LIMIT,
    DIRECTOR_REASONING_SOFT_LIMIT,
    GUIDANCE_NAME_ACCEPT_LIMIT,
    GUIDANCE_NAME_HARD_LIMIT,
    GUIDANCE_PURPOSE_ACCEPT_LIMIT,
    GUIDANCE_PURPOSE_HARD_LIMIT,
    GUIDANCE_PURPOSE_SOFT_LIMIT,
    DirectorDecisionBatch,
    DirectorDecisionBatchLoose,
    DirectorGuidance,
    DirectorGuidanceWire,
    DirectorObservation,
    DirectorSkillRequest,
    DirectorSkillRequestWire,
)
from app.domain.director_npc.schemas import (
    ABILITY_DESCRIPTION_ACCEPT_LIMIT,
    ABILITY_DESCRIPTION_SOFT_LIMIT,
    ABILITY_NAME_ACCEPT_LIMIT,
    PERSONALITY_TRAIT_ACCEPT_LIMIT,
    PERSONALITY_TRAIT_SOFT_LIMIT,
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
            warnings: list[DirectorWarning] = []
            retry_errors: list[str] = []
            reasoning = (
                output.reasoning.strip()
                if output.reasoning and output.reasoning.strip()
                else _FALLBACK_REASONING
            )
            # reasoning 是 soft_text：首轮超过 Accept Limit 触发一次反馈重试；
            # 第二轮保留到 Salvage Limit，超出才截断，不再静默替换 fallback。
            # 为保证 salvage 记录永不超出领域上限 S，即使首轮为触发重试
            # 超过 S 的值也在入 record 前截断（不影响重试判定）。
            orig_len = len(reasoning)
            if orig_len > DIRECTOR_REASONING_SOFT_LIMIT:
                warnings.append(DirectorWarning(
                    code="director_reasoning_truncated",
                    message=(
                        f"reasoning exceeded the salvage limit "
                        f"{DIRECTOR_REASONING_SOFT_LIMIT} and was truncated."
                    ),
                ))
                reasoning = reasoning[:DIRECTOR_REASONING_SOFT_LIMIT]
            if current_attempt == 1 and orig_len > DIRECTOR_REASONING_ACCEPT_LIMIT:
                retry_errors.append(
                    f"reasoning length {orig_len} exceeds accept limit "
                    f"{DIRECTOR_REASONING_ACCEPT_LIMIT}; return to the prompt "
                    f"target {DIRECTOR_REASONING_HARD_LIMIT}"
                )

            top_warnings: list[DirectorWarning] = []
            extra_keys = sorted((output.model_extra or {}).keys())
            if extra_keys:
                top_warnings.append(DirectorWarning(
                    code="director_top_level",
                    message=f"unexpected top-level fields: {extra_keys}",
                ))

            requests: list[DirectorSkillRequest] = []
            dropped_warnings: list[DirectorWarning] = []
            retry_errors = [
                *retry_errors,
                *[warning.message for warning in top_warnings],
            ]
            for index, raw in enumerate(output.requests):
                try:
                    request, request_warnings, request_errors = (
                        _parse_request_wire(raw, attempt=current_attempt)
                    )
                except ValidationError as exc:
                    retry_errors.append(f"request[{index}]: {exc}")
                    dropped_warnings.append(DirectorWarning(
                        code="director_request_dropped",
                        message=f"request[{index}] dropped: {exc}",
                    ))
                    continue
                warnings.extend(request_warnings)
                if request is None:
                    retry_errors.extend(request_errors)
                    dropped_warnings.append(DirectorWarning(
                        code="director_request_dropped",
                        message=(
                            f"request[{index}] dropped: "
                            + "; ".join(request_errors)
                        ),
                    ))
                    continue
                candidate = [*requests, request]
                valid, batch_error = _batch_valid(
                    self._skills, candidate, observation.phase
                )
                if not valid:
                    detail = batch_error or "conflicts with earlier requests"
                    retry_errors.append(f"request[{index}]: {detail}")
                    dropped_warnings.append(DirectorWarning(
                        code="director_request_dropped",
                        message=(
                            f"request[{index}] dropped: {detail}"
                        ),
                    ))
                    continue
                requests = candidate

            attempt_records[current_attempt] = _AttemptRecord(
                reasoning=reasoning,
                requests=requests,
                warnings=[*top_warnings, *warnings, *dropped_warnings],
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
                        "output_schema_json": display_schema_json(
                            DirectorDecisionBatch,
                            max_lengths={
                                "reasoning": DIRECTOR_REASONING_HARD_LIMIT,
                                ("requests", "guidance", "name"):
                                    GUIDANCE_NAME_HARD_LIMIT,
                                ("requests", "guidance", "purpose"):
                                    GUIDANCE_PURPOSE_HARD_LIMIT,
                            },
                        ),
                    },
                    output_schema=DirectorDecisionBatchLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    temperature=0.4,
                    trace_id=trace_id,
                ),
            )
            record = attempt_records[current_attempt]
            self.last_warnings = list(record.warnings)
            return DirectorDecisionBatch(
                reasoning=record.reasoning,
                requests=record.requests,
            )
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
            ValueError,
        ) as exc:
            return salvage(exc)



def _parse_request_wire(
    raw: Any,
    *,
    attempt: int,
) -> tuple[DirectorSkillRequest | None, list[DirectorWarning], list[str]]:
    """Parse one request through the tolerant wire model with H/A/S rules.

    Returns ``(request, warnings, retry_errors)``. ``request is None`` means
    the item was dropped at module granularity (strict_name still above its
    Accept Limit after retries, or a structural failure on the final attempt).
    """

    warnings: list[DirectorWarning] = []
    errors: list[str] = []
    if isinstance(raw, dict):
        raw = dict(raw)
        for key in ("request_id", "skill"):
            if isinstance(raw.get(key), str):
                raw[key] = raw[key].strip()
        guidance = raw.get("guidance")
        if isinstance(guidance, str):
            raw["guidance"] = guidance.strip()
        elif isinstance(guidance, DirectorGuidance):
            raw["guidance"] = guidance.model_dump()
    try:
        wire = DirectorSkillRequestWire.model_validate(raw)
    except ValidationError as exc:
        return None, warnings, [str(exc)]

    # identifier / reference fields stay strict: no buffer, no truncation.
    guidance = wire.guidance
    if isinstance(guidance, DirectorGuidanceWire):
        name = guidance.name.strip()
        purpose = guidance.purpose.strip()
        description = guidance.description
        if attempt == 1:
            if len(name) > GUIDANCE_NAME_ACCEPT_LIMIT:
                errors.append(
                    f"guidance.name length {len(name)} exceeds accept limit "
                    f"{GUIDANCE_NAME_ACCEPT_LIMIT}; return to the prompt "
                    f"target {GUIDANCE_NAME_HARD_LIMIT}"
                )
            if len(purpose) > GUIDANCE_PURPOSE_ACCEPT_LIMIT:
                errors.append(
                    f"guidance.purpose length {len(purpose)} exceeds accept "
                    f"limit {GUIDANCE_PURPOSE_ACCEPT_LIMIT}; return to the "
                    f"prompt target {GUIDANCE_PURPOSE_HARD_LIMIT}"
                )
            if errors:
                return None, warnings, errors
        else:
            if len(name) > GUIDANCE_NAME_ACCEPT_LIMIT:
                # strict_name: never truncated; the whole request item is
                # dropped after retries are exhausted.
                return None, warnings, [
                    f"guidance.name length {len(name)} still exceeds accept "
                    f"limit {GUIDANCE_NAME_ACCEPT_LIMIT} after retries"
                ]
            if len(purpose) > GUIDANCE_PURPOSE_SOFT_LIMIT:
                warnings.append(DirectorWarning(
                    code="director_guidance_truncated",
                    message=(
                        "guidance.purpose exceeded the salvage limit "
                        f"{GUIDANCE_PURPOSE_SOFT_LIMIT} and was truncated."
                    ),
                ))
                purpose = purpose[:GUIDANCE_PURPOSE_SOFT_LIMIT]
        # description 是后端最终丢弃的 honeypot：超长不拖垮合法请求，
        # 转换时直接按领域上限截去（值不会被转发）。
        domain_guidance = DirectorGuidance(
            name=name,
            purpose=purpose,
            description=description[:GUIDANCE_PURPOSE_SOFT_LIMIT],
        )
    else:
        domain_guidance = guidance.strip()

    arguments = _normalize_spontaneous_arguments(
        wire.skill,
        wire.arguments,
        attempt=attempt,
        warnings=warnings,
        errors=errors,
    )
    if errors:
        return None, warnings, errors
    return DirectorSkillRequest(
        request_id=wire.request_id,
        skill=wire.skill,
        arguments=arguments,
        guidance=domain_guidance,
    ), warnings, []


def _normalize_spontaneous_arguments(
    skill: str,
    arguments: dict[str, Any],
    *,
    attempt: int,
    warnings: list[DirectorWarning],
    errors: list[str],
) -> dict[str, Any]:
    """Apply the H/A/S length policy to ``introduce_npc.spontaneous`` fields.

    personality_trait: soft_text 40/50/60; ability.name: strict_name 20/25;
    ability.description: soft_text 120/150/180. First attempt above A records
    a retry error; on retries exhausted soft_text is truncated to S and
    strict_name items that still exceed A are dropped whole.
    """

    if skill != "introduce_npc":
        return arguments
    spontaneous = arguments.get("spontaneous")
    if not isinstance(spontaneous, dict):
        return arguments
    spontaneous = dict(spontaneous)

    personality = spontaneous.get("personality_trait")
    if isinstance(personality, str):
        clean = personality.strip()
        if attempt == 1 and len(clean) > PERSONALITY_TRAIT_ACCEPT_LIMIT:
            errors.append(
                f"spontaneous.personality_trait length {len(clean)} exceeds "
                f"accept limit {PERSONALITY_TRAIT_ACCEPT_LIMIT}; return to "
                "the prompt target"
            )
        elif attempt > 1 and len(clean) > PERSONALITY_TRAIT_SOFT_LIMIT:
            warnings.append(DirectorWarning(
                code="director_spontaneous_truncated",
                message=(
                    "spontaneous.personality_trait exceeded the salvage "
                    f"limit {PERSONALITY_TRAIT_SOFT_LIMIT} and was truncated."
                ),
            ))
            clean = clean[:PERSONALITY_TRAIT_SOFT_LIMIT]
        spontaneous["personality_trait"] = clean

    abilities = spontaneous.get("known_abilities")
    if isinstance(abilities, list):
        kept: list[Any] = []
        for index, ability in enumerate(abilities):
            if not isinstance(ability, dict):
                kept.append(ability)
                continue
            ability = dict(ability)
            name = ability.get("name")
            if isinstance(name, str):
                name_clean = name.strip()
                if len(name_clean) > ABILITY_NAME_ACCEPT_LIMIT:
                    if attempt == 1:
                        errors.append(
                            f"spontaneous.known_abilities[{index}].name "
                            f"length {len(name_clean)} exceeds accept limit "
                            f"{ABILITY_NAME_ACCEPT_LIMIT}; return to the "
                            "prompt target"
                        )
                    else:
                        warnings.append(DirectorWarning(
                            code="director_spontaneous_ability_dropped",
                            message=(
                                f"spontaneous.known_abilities[{index}] "
                                "dropped: name still exceeds accept limit "
                                f"{ABILITY_NAME_ACCEPT_LIMIT} after retries"
                            ),
                        ))
                    continue
                ability["name"] = name_clean
            description = ability.get("description")
            if isinstance(description, str):
                desc_clean = description.strip()
                if (
                    attempt == 1
                    and len(desc_clean) > ABILITY_DESCRIPTION_ACCEPT_LIMIT
                ):
                    errors.append(
                        f"spontaneous.known_abilities[{index}].description "
                        f"length {len(desc_clean)} exceeds accept limit "
                        f"{ABILITY_DESCRIPTION_ACCEPT_LIMIT}; return to the "
                        "prompt target"
                    )
                elif (
                    attempt > 1
                    and len(desc_clean) > ABILITY_DESCRIPTION_SOFT_LIMIT
                ):
                    warnings.append(DirectorWarning(
                        code="director_spontaneous_truncated",
                        message=(
                            f"spontaneous.known_abilities[{index}].description "
                            f"exceeded the salvage limit "
                            f"{ABILITY_DESCRIPTION_SOFT_LIMIT} and was "
                            "truncated."
                        ),
                    ))
                    desc_clean = desc_clean[:ABILITY_DESCRIPTION_SOFT_LIMIT]
                ability["description"] = desc_clean
            kept.append(ability)
        spontaneous["known_abilities"] = kept

    arguments = dict(arguments)
    arguments["spontaneous"] = spontaneous
    return arguments


def _batch_valid(
    service: DirectorSkillService,
    requests: list[DirectorSkillRequest],
    phase: str,
) -> tuple[bool, str | None]:
    try:
        service.validate_batch(
            DirectorDecisionBatch(
                reasoning=_FALLBACK_REASONING,
                requests=requests,
            ),
            phase=phase,
        )
        return True, None
    except (ValueError, ValidationError) as exc:
        return False, str(exc)
