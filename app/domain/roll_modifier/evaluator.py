from collections import defaultdict
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.roll_modifier.schemas import (
    LLMRollModifierBatch,
    RollModifier,
    RollModifierBatch,
)
from app.domain.views import project_action_parse, project_current_state


class RollModifierEvaluator:
    def __init__(self, gateway: LLMGateway):
        self.gateway = gateway

    def evaluate(
        self,
        parse_result: ActionParseResult,
        current_state: CurrentState,
        core_memory: CoreMemory,
        *,
        roll_actions: list[AtomicAction] | None = None,
        trace_id: str | None = None,
    ) -> RollModifierBatch:
        actions = roll_actions if roll_actions is not None else [
            action for action in parse_result.atomic_actions if action.likely_needs_roll
        ]
        action_ids = {action.action_id for action in actions}
        if not action_ids:
            return RollModifierBatch()

        roll_parse_result = parse_result.model_copy(
            update={"atomic_actions": actions},
            deep=True,
        )
        llm_result = cast(
            LLMRollModifierBatch,
            self.gateway.run(
                task="roll_modifier",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(current_state),
                    "action_parse_result_json": project_action_parse(roll_parse_result),
                    "output_schema_json": schema_json(LLMRollModifierBatch),
                },
                output_schema=LLMRollModifierBatch,
                trace_id=trace_id,
            ),
        )

        modifiers = [
            RollModifier(
                action_id=modifier.action_id,
                source="situational",
                label=modifier.label,
                reason=modifier.reason,
                value=modifier.value,
            )
            for modifier in llm_result.modifiers
            if modifier.action_id in action_ids
        ]
        return RollModifierBatch(modifiers=self._validate_and_trim(modifiers, action_ids))

    @staticmethod
    def _validate_and_trim(
        modifiers: list[RollModifier],
        action_ids: set[str],
    ) -> list[RollModifier]:
        grouped: dict[str, list[RollModifier]] = defaultdict(list)
        for modifier in modifiers:
            if modifier.action_id not in action_ids:
                continue
            grouped[modifier.action_id].append(modifier)

        validated: list[RollModifier] = []
        for action_id in sorted(grouped):
            action_modifiers = grouped[action_id]
            if len(action_modifiers) > 2:
                action_modifiers = sorted(
                    action_modifiers,
                    key=lambda m: abs(m.value),
                    reverse=True,
                )[:2]
            total = sum(modifier.value for modifier in action_modifiers)
            if abs(total) > 8:
                raise ValueError(f"modifier total for action {action_id} exceeds 8")
            validated.extend(action_modifiers)
        return validated
