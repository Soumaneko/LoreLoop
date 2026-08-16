from __future__ import annotations

from typing import Literal


FailureDecision = Literal[
    "no_effect",
    "correctable",
    "degradable",
    "integrity",
]

FailureAction = Literal[
    "continue_with_warning",
    "retry_with_feedback",
    "degrade",
    "abort_and_rollback",
]

_VALID_DECISIONS = frozenset({
    "no_effect",
    "correctable",
    "degradable",
    "integrity",
})


def decide_failure_action(
    decision: FailureDecision,
    attempt: int,
) -> FailureAction:
    """Map a module failure decision and 1-based attempt to a base action.

    The default base policy gives a correctable failure exactly one feedback
    retry; the second attempt (and any later attempt) degrades instead of
    retrying again. Permanent provider errors are not a failure decision here:
    they are classified by the LLM provider classifier, and the module then
    chooses ``degradable``.

    Unknown decisions and non-positive attempts are programming errors and
    raise ``ValueError`` rather than silently degrading.
    """
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"unknown failure decision: {decision!r}")
    if not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    if decision == "no_effect":
        return "continue_with_warning"
    if decision == "correctable":
        return "retry_with_feedback" if attempt == 1 else "degrade"
    if decision == "degradable":
        return "degrade"
    return "abort_and_rollback"
