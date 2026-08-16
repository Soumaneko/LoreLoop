import pytest

from app.core.failure_policy import decide_failure_action


@pytest.mark.parametrize(
    ("decision", "attempt", "expected"),
    [
        ("no_effect", 1, "continue_with_warning"),
        ("no_effect", 2, "continue_with_warning"),
        ("correctable", 1, "retry_with_feedback"),
        ("correctable", 2, "degrade"),
        ("correctable", 3, "degrade"),
        ("degradable", 1, "degrade"),
        ("degradable", 2, "degrade"),
        ("integrity", 1, "abort_and_rollback"),
    ],
)
def test_decide_failure_action_table(decision, attempt, expected):
    assert decide_failure_action(decision, attempt) == expected


def test_decide_failure_action_rejects_unknown_decision():
    with pytest.raises(ValueError, match="unknown failure decision"):
        decide_failure_action("integrity_typo", 1)


@pytest.mark.parametrize("attempt", [0, -1])
def test_decide_failure_action_rejects_non_positive_attempt(attempt):
    with pytest.raises(ValueError, match="positive integer"):
        decide_failure_action("degradable", attempt)
