from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import CurrentState, InventoryItem, ParticipantState, TimedCondition


class StateChangeError(ValueError):
    pass


@dataclass(frozen=True)
class StateChangeWarning:
    operation_id: str
    operation_type: str
    target: str
    code: str
    message: str


@dataclass(frozen=True)
class StateApplyResult:
    state: CurrentState
    warnings: list[StateChangeWarning] = field(default_factory=list)


class _Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_fact_ids: list[str] = Field(min_length=1)


class LocationChange(_Operation):
    type: Literal["location_change"]
    location: str = Field(min_length=1)


class ParticipantAdd(_Operation):
    type: Literal["participant_add"]
    name: str = Field(min_length=1)


class ParticipantRemove(_Operation):
    type: Literal["participant_remove"]
    name: str = Field(min_length=1)


class SceneConditionAdd(_Operation):
    type: Literal["scene_condition_add"]
    condition: str = Field(min_length=1)
    expires_after_phases: int = Field(ge=-1)

    @model_validator(mode="after")
    def _expiry_must_be_permanent_or_positive(self):
        if self.expires_after_phases == 0:
            raise ValueError("expires_after_phases must be -1 or positive")
        return self


class SceneConditionRemove(_Operation):
    type: Literal["scene_condition_remove"]
    condition: str = Field(min_length=1)


class NPCConditionAdd(_Operation):
    type: Literal["npc_condition_add"]
    name: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    expires_after_phases: int = Field(ge=-1)

    @model_validator(mode="after")
    def _expiry_must_be_permanent_or_positive(self):
        if self.expires_after_phases == 0:
            raise ValueError("expires_after_phases must be -1 or positive")
        return self


class NPCConditionRemove(_Operation):
    type: Literal["npc_condition_remove"]
    name: str = Field(min_length=1)
    condition: str = Field(min_length=1)


class ParticipantAttitudeChange(_Operation):
    type: Literal["participant_attitude_change"]
    name: str = Field(min_length=1)
    attitude_to_player: str | None = None


class WorldClockAdvance(_Operation):
    type: Literal["world_clock_advance"]
    phases: int = Field(ge=1)


class PlayerConditionAdd(_Operation):
    type: Literal["player_condition_add"]
    condition: str = Field(min_length=1)


class PlayerConditionRemove(_Operation):
    type: Literal["player_condition_remove"]
    condition: str = Field(min_length=1)


class PlayerVisibleStatusChange(_Operation):
    type: Literal["player_visible_status_change"]
    visible_status: str | None = None


class InventoryDelta(_Operation):
    type: Literal["inventory_delta"]
    item: str = Field(min_length=1)
    delta: int

    @model_validator(mode="after")
    def _delta_must_not_be_zero(self):
        if self.delta == 0:
            raise ValueError("inventory delta must not be zero")
        return self


class MoneyDelta(_Operation):
    type: Literal["money_delta"]
    delta: int

    @model_validator(mode="after")
    def _delta_must_not_be_zero(self):
        if self.delta == 0:
            raise ValueError("money delta must not be zero")
        return self


class FixedAssetAdd(_Operation):
    type: Literal["fixed_asset_add"]
    asset: str = Field(min_length=1)


class FixedAssetRemove(_Operation):
    type: Literal["fixed_asset_remove"]
    asset: str = Field(min_length=1)


class TemporaryAssetAdd(_Operation):
    type: Literal["temporary_asset_add"]
    asset: str = Field(min_length=1)


class TemporaryAssetRemove(_Operation):
    type: Literal["temporary_asset_remove"]
    asset: str = Field(min_length=1)


StateOperation = Annotated[
    Union[
        LocationChange,
        ParticipantAdd,
        ParticipantRemove,
        SceneConditionAdd,
        SceneConditionRemove,
        NPCConditionAdd,
        NPCConditionRemove,
        ParticipantAttitudeChange,
        WorldClockAdvance,
        PlayerConditionAdd,
        PlayerConditionRemove,
        PlayerVisibleStatusChange,
        InventoryDelta,
        MoneyDelta,
        FixedAssetAdd,
        FixedAssetRemove,
        TemporaryAssetAdd,
        TemporaryAssetRemove,
    ],
    Field(discriminator="type"),
]


class StateChangeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1)
    operations: list[StateOperation] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _operation_ids_must_be_unique(self):
        ids = [operation.operation_id for operation in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("operation_id values must be unique")
        return self


def apply_state_changes(
    current_state: CurrentState,
    change_set: StateChangeSet,
    *,
    npc_aliases: dict[str, list[str]] | None = None,
) -> StateApplyResult:
    state = current_state.model_copy(deep=True)
    aliases = _alias_map(npc_aliases or {})
    warnings: list[StateChangeWarning] = []
    clock_advance_phases = 0

    for operation in change_set.operations:
        if isinstance(operation, LocationChange):
            if _norm(operation.location) == _norm(state.scene.location):
                raise StateChangeError("location_change does not change the location")
            state.scene.location = operation.location.strip()
            state.scene.scene_id = _increment_scene_id(state.scene.scene_id)
        elif isinstance(operation, ParticipantAdd):
            if _find_participant(state, operation.name, aliases) is not None:
                raise StateChangeError(f"participant already present: {operation.name}")
            canonical_name = aliases.get(_norm(operation.name), operation.name.strip())
            state.scene.participants.append(ParticipantState(name=canonical_name))
        elif isinstance(operation, ParticipantRemove):
            participant = _require_participant(state, operation.name, aliases)
            if _norm(participant.name) == _norm(state.player.name):
                raise StateChangeError("the player cannot be removed from participants")
            state.scene.participants.remove(participant)
        elif isinstance(operation, SceneConditionAdd):
            _add_timed_condition(
                state.scene.scene_condition,
                operation.condition,
                operation.expires_after_phases,
            )
        elif isinstance(operation, SceneConditionRemove):
            _remove_timed_condition(
                state.scene.scene_condition,
                operation.condition,
                "scene condition",
            )
        elif isinstance(operation, NPCConditionAdd):
            participant = _resolve_npc_attachment_target(
                state,
                operation,
                aliases,
                warnings,
            )
            if participant is None:
                continue
            _add_timed_condition(
                participant.npc_conditions,
                operation.condition,
                operation.expires_after_phases,
            )
        elif isinstance(operation, NPCConditionRemove):
            participant = _resolve_npc_attachment_target(
                state,
                operation,
                aliases,
                warnings,
            )
            if participant is None:
                continue
            _remove_timed_condition(
                participant.npc_conditions,
                operation.condition,
                "NPC condition",
            )
        elif isinstance(operation, ParticipantAttitudeChange):
            participant = _resolve_npc_attachment_target(
                state,
                operation,
                aliases,
                warnings,
            )
            if participant is None:
                continue
            updated_attitude = _clean_optional(operation.attitude_to_player)
            if updated_attitude == participant.attitude_to_player:
                raise StateChangeError(
                    f"participant attitude does not change: {participant.name}"
                )
            participant.attitude_to_player = updated_attitude
        elif isinstance(operation, WorldClockAdvance):
            clock_advance_phases += operation.phases
        elif isinstance(operation, PlayerConditionAdd):
            _add_unique(state.player.condition, operation.condition)
        elif isinstance(operation, PlayerConditionRemove):
            _remove_existing(state.player.condition, operation.condition, "condition")
        elif isinstance(operation, PlayerVisibleStatusChange):
            updated_status = _clean_optional(operation.visible_status)
            if updated_status == state.player.visible_status:
                raise StateChangeError("player visible status does not change")
            state.player.visible_status = updated_status
        elif isinstance(operation, InventoryDelta):
            _apply_inventory_delta(state, operation.item, operation.delta)
        elif isinstance(operation, MoneyDelta):
            _apply_money_delta(state, operation.delta)
        elif isinstance(operation, FixedAssetAdd):
            _add_unique(state.resources.fixed_assets, operation.asset)
        elif isinstance(operation, FixedAssetRemove):
            _remove_existing(state.resources.fixed_assets, operation.asset, "fixed asset")
        elif isinstance(operation, TemporaryAssetAdd):
            _add_unique(state.resources.temporary_assets, operation.asset)
        elif isinstance(operation, TemporaryAssetRemove):
            _remove_existing(
                state.resources.temporary_assets,
                operation.asset,
                "temporary asset",
            )

    if clock_advance_phases:
        state.world_clock = state.world_clock.advance_phase(clock_advance_phases)
        _advance_condition_expiries(state, clock_advance_phases)

    return StateApplyResult(
        state=CurrentState.model_validate(state.model_dump(mode="python")),
        warnings=warnings,
    )


def _apply_inventory_delta(state: CurrentState, item_name: str, delta: int) -> None:
    item = next(
        (item for item in state.resources.inventory if _norm(item.name) == _norm(item_name)),
        None,
    )
    if item is None:
        if delta < 0:
            raise StateChangeError(f"cannot remove missing inventory item: {item_name}")
        state.resources.inventory.append(
            InventoryItem(name=item_name.strip(), quantity=delta)
        )
        return

    quantity = item.quantity + delta
    if quantity < 0:
        raise StateChangeError(
            f"inventory delta exceeds quantity for {item.name}: {item.quantity} + {delta}"
        )
    if quantity == 0:
        state.resources.inventory.remove(item)
    else:
        item.quantity = quantity


def _apply_money_delta(state: CurrentState, delta: int) -> None:
    current = state.resources.money
    if current is None:
        if delta < 0:
            raise StateChangeError("cannot spend money when the balance is unknown")
        current = 0
    updated = current + delta
    if updated < 0:
        raise StateChangeError(f"money delta would create a negative balance: {updated}")
    state.resources.money = updated


def _add_timed_condition(
    conditions: list[TimedCondition],
    condition: str,
    expires_after_phases: int,
) -> None:
    clean = condition.strip()
    if any(_norm(item.condition) == _norm(clean) for item in conditions):
        raise StateChangeError(f"condition already exists: {clean}")
    conditions.append(
        TimedCondition(
            condition=clean,
            expires_after_phases=expires_after_phases,
        )
    )


def _remove_timed_condition(
    conditions: list[TimedCondition],
    condition: str,
    label: str,
) -> None:
    existing = next(
        (
            item
            for item in conditions
            if _norm(item.condition) == _norm(condition)
        ),
        None,
    )
    if existing is None:
        raise StateChangeError(f"cannot remove missing {label}: {condition}")
    conditions.remove(existing)


def _advance_condition_expiries(state: CurrentState, phases: int) -> None:
    condition_lists = [
        state.scene.scene_condition,
        *[
            participant.npc_conditions
            for participant in state.scene.participants
        ],
    ]
    for conditions in condition_lists:
        retained: list[TimedCondition] = []
        for item in conditions:
            if item.expires_after_phases == -1:
                retained.append(item)
                continue
            remaining = item.expires_after_phases - phases
            if remaining > 0:
                retained.append(
                    item.model_copy(update={"expires_after_phases": remaining})
                )
        conditions[:] = retained


def _find_participant(
    state: CurrentState,
    name: str,
    aliases: dict[str, str],
) -> ParticipantState | None:
    target = _norm(aliases.get(_norm(name), name))
    for participant in state.scene.participants:
        canonical = _norm(
            aliases.get(_norm(participant.name), participant.name)
        )
        if canonical == target:
            return participant
    return None


def _require_participant(
    state: CurrentState,
    name: str,
    aliases: dict[str, str],
) -> ParticipantState:
    participant = _find_participant(state, name, aliases)
    if participant is None:
        raise StateChangeError(f"participant is not present: {name}")
    return participant


def _resolve_npc_attachment_target(
    state: CurrentState,
    operation: NPCConditionAdd | NPCConditionRemove | ParticipantAttitudeChange,
    aliases: dict[str, str],
    warnings: list[StateChangeWarning],
) -> ParticipantState | None:
    participant = _find_participant(state, operation.name, aliases)
    if participant is None:
        target = operation.name.strip()
        warnings.append(StateChangeWarning(
            operation_id=operation.operation_id,
            operation_type=operation.type,
            target=target,
            code="npc_target_not_present",
            message=(
                f"NPC attachment skipped because the target is not present: {target}"
            ),
        ))
        return None
    if _norm(participant.name) == _norm(state.player.name):
        raise StateChangeError(
            "player condition/status must use player-specific operations"
        )
    return participant


def _alias_map(npc_aliases: dict[str, list[str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, alias_values in npc_aliases.items():
        canonical_key = _norm(name)
        canonical_name = str(name).strip()
        if not canonical_key:
            continue
        result[canonical_key] = canonical_name
        for alias in alias_values:
            if _norm(alias):
                result[_norm(alias)] = canonical_name
    return result


def _add_unique(values: list[str], value: str) -> None:
    clean = value.strip()
    if any(_norm(existing) == _norm(clean) for existing in values):
        raise StateChangeError(f"value already exists: {clean}")
    values.append(clean)


def _remove_existing(values: list[str], value: str, label: str) -> None:
    existing = next((item for item in values if _norm(item) == _norm(value)), None)
    if existing is None:
        raise StateChangeError(f"cannot remove missing {label}: {value}")
    values.remove(existing)


def _clean_optional(value: str | None) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _norm(value: str) -> str:
    return str(value).strip().casefold()


def _increment_scene_id(scene_id: str) -> str:
    match = re.search(r"(\d+)$", scene_id)
    if match:
        number = int(match.group(1)) + 1
        return scene_id[: match.start()] + str(number).zfill(len(match.group(1)))
    return scene_id + "_1"
