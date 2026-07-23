from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RecoveryKind = Literal["generation", "confirmation", "initialization"]


@dataclass(frozen=True)
class OperationPolicy:
    operation_type: str
    exclusive_key: str
    requires_ack: bool
    player_visible: bool = False
    recovery_kind: RecoveryKind | None = None
    supersedes: tuple[str, ...] = ()


OPERATION_POLICIES = {
    policy.operation_type: policy
    for policy in (
        OperationPolicy(
            "generation",
            "generation",
            True,
            player_visible=True,
            recovery_kind="generation",
        ),
        OperationPolicy(
            "rephrase",
            "generation",
            True,
            player_visible=True,
            recovery_kind="generation",
            supersedes=("generation", "rephrase", "reroll", "feedback_rewrite"),
        ),
        OperationPolicy(
            "reroll",
            "generation",
            True,
            player_visible=True,
            recovery_kind="generation",
            supersedes=("generation", "rephrase", "reroll", "feedback_rewrite"),
        ),
        OperationPolicy(
            "feedback_rewrite",
            "generation",
            True,
            player_visible=True,
            recovery_kind="generation",
            supersedes=("generation", "rephrase", "reroll", "feedback_rewrite"),
        ),
        OperationPolicy(
            "confirmation",
            "confirmation",
            True,
            player_visible=True,
            recovery_kind="confirmation",
            supersedes=(
                "generation",
                "rephrase",
                "reroll",
                "feedback_rewrite",
                "initialization_opening",
            ),
        ),
        OperationPolicy(
            "initialization_story_direction",
            "initialization",
            True,
            player_visible=True,
            recovery_kind="initialization",
        ),
        OperationPolicy(
            "initialization_opening",
            "initialization",
            True,
            player_visible=True,
            recovery_kind="initialization",
            supersedes=("initialization_story_direction",),
        ),
        OperationPolicy(
            "maintenance_batch_classify_manual",
            "manual_maintenance",
            True,
        ),
        OperationPolicy(
            "maintenance_memory_maintain_manual",
            "manual_maintenance",
            True,
        ),
    )
}


def operation_policy(operation_type: str) -> OperationPolicy:
    try:
        return OPERATION_POLICIES[operation_type]
    except KeyError as exc:
        raise ValueError(f"Unknown managed operation type: {operation_type}") from exc


PLAYER_OPERATION_TYPES = frozenset(
    operation_type
    for operation_type, policy in OPERATION_POLICIES.items()
    if policy.player_visible
)
