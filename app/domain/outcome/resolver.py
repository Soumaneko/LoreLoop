from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.current_state.schemas import CurrentState
from app.domain.outcome.schemas import (
    InformationPolicy,
    OutcomeBand,
    OutcomeFrame,
    OutcomeResolutionResult,
    RiskLevel,
)
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.roll.schemas import RollResultBatch


CONSEQUENCE_BY_BAND: dict[OutcomeBand, str] = {
    "critical_failure": "The action must fail and introduce a serious complication.",
    "failure": "The action must fail or produce an unfavorable consequence.",
    "partial_success": "The action may partly succeed, but must include a cost, risk, delay, or complication.",
    "success": "The action should succeed in a clear but grounded way.",
    "critical_success": "The action should succeed strongly and may create an additional advantage.",
}


def map_roll_to_band(value: int, sides: int) -> OutcomeBand:
    if sides <= 0:
        raise ValueError("dice sides must be positive")
    if value < 1 or value > sides:
        raise ValueError(f"roll value must be between 1 and {sides}")

    ratio = value / sides

    if ratio <= 0.05:
        return "critical_failure"
    if ratio <= 0.45:
        return "failure"
    if ratio <= 0.7:
        return "partial_success"
    if ratio <= 0.95:
        return "success"
    return "critical_success"


def build_information_policy(band: OutcomeBand) -> InformationPolicy:
    if band == "critical_failure":
        return "none"
    if band == "failure":
        return "ambiguous_hint_only"
    if band == "partial_success":
        return "partial_disclosure_with_cost"
    if band == "success":
        return "clear_answer_allowed"
    return "clear_answer_with_advantage"


def infer_risk_level(action: AtomicAction) -> RiskLevel:
    if action.action_type in {"attack", "defend", "cast_or_use_power", "hide_or_stealth"}:
        return "high"
    if action.action_type in {"speak", "observe", "inspect_object", "plan", "wait"}:
        return "low"
    return "medium"


def build_permissions(band: OutcomeBand) -> list[str]:
    if band == "critical_failure":
        return [
            "The outline may introduce a serious immediate complication.",
            "The outline may worsen position, safety, resources, or relationships.",
        ]
    if band == "failure":
        return [
            "The outline may block the intended goal.",
            "The outline may introduce an unfavorable consequence.",
        ]
    if band == "partial_success":
        return [
            "The outline may grant part of the intended goal.",
            "The outline must attach a cost, risk, delay, or complication.",
        ]
    if band == "success":
        return [
            "The outline may grant the intended goal in a clear but grounded way.",
        ]
    return [
        "The outline may grant the intended goal strongly.",
        "The outline may add one grounded advantage.",
    ]


def build_constraints(band: OutcomeBand) -> list[str]:
    if band in {"critical_failure", "failure"}:
        return [
            "Do not frame this action as a clean success.",
            "Do not erase the failed outcome with immediate luck or unrelated help.",
        ]
    if band == "partial_success":
        return [
            "Do not frame this action as a clean success.",
            "Include an explicit cost, risk, delay, or complication.",
        ]
    if band == "success":
        return [
            "Keep the success consistent with CurrentState and CoreMemory.",
            "Do not add an unrelated bonus advantage.",
        ]
    return [
        "Keep the strong success consistent with CurrentState and CoreMemory.",
        "Any additional advantage must follow from the action.",
    ]


class OutcomeResolver:
    def resolve(
        self,
        *,
        parse_result: ActionParseResult,
        roll_results: RollResultBatch,
        current_state: CurrentState,
        retrieved_memories: CandidateRetrievalResult | None = None,
    ) -> OutcomeResolutionResult:
        _ = current_state
        _ = retrieved_memories
        action_by_id = {
            action.action_id: action
            for action in parse_result.atomic_actions
        }

        outcomes: list[OutcomeFrame] = []

        for roll in roll_results.results:
            action = action_by_id.get(roll.action_id)
            if action is None:
                continue

            band = map_roll_to_band(
                value=roll.value,
                sides=roll.dice_sides,
            )

            outcomes.append(
                OutcomeFrame(
                    action_id=roll.action_id,
                    sequence=roll.sequence,
                    actor=roll.actor,
                    action_type=roll.action_type,
                    dice=roll.dice,
                    roll_value=roll.value,
                    dice_sides=roll.dice_sides,
                    outcome_band=band,
                    required_consequence=CONSEQUENCE_BY_BAND[band],
                    information_policy=build_information_policy(band),
                    risk_level=infer_risk_level(action),
                    narrative_permission=build_permissions(band),
                    narrative_constraints=build_constraints(band),
                )
            )

        return OutcomeResolutionResult(
            outcomes=outcomes,
            total_outcomes=len(outcomes),
        )
