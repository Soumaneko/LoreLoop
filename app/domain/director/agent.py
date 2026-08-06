from __future__ import annotations

import json
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json

from .schemas import DirectorDecisionBatch, DirectorObservation
from .skills import DirectorSkillService


class DirectorAgent:
    """Chooses a bounded batch from the skills available in the current phase."""

    def __init__(
        self,
        llm: LLMGateway,
        skills: DirectorSkillService,
    ) -> None:
        self._llm = llm
        self._skills = skills

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
        available_skills = self._skills.specs_for_phase(observation.phase)
        if not available_skills:
            return DirectorDecisionBatch(
                reasoning="当前阶段没有可用的导演技能。",
            )

        result = cast(
            DirectorDecisionBatch,
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
                    "output_schema_json": schema_json(DirectorDecisionBatch),
                },
                output_schema=DirectorDecisionBatch,
                temperature=0.4,
                trace_id=trace_id,
                output_validator=lambda output: self._skills.validate_batch(
                    cast(DirectorDecisionBatch, output),
                    phase=observation.phase,
                ),
            ),
        )
        return result
