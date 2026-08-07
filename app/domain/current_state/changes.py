from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.text import normalize_key
from .schemas import CurrentState, InventoryItem, ParticipantState


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


class ParticipantAttitudeChange(_Operation):
    type: Literal["participant_attitude_change"]
    name: str = Field(min_length=1)
    attitude_to_player: str | None = None


class WorldClockAdvance(_Operation):
    type: Literal["world_clock_advance"]
    phases: int = Field(ge=1)


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
        ParticipantAttitudeChange,
        WorldClockAdvance,
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

    # Two-pass execution: settle/gain operations first, then the modify/consume
    # operations that depend on the settled state. This keeps the intent of a
    # misordered batch (e.g. attitude before participant_add, consume before
    # obtain) instead of silently dropping the dependent operation.
    for operation in change_set.operations:
        if _defer_to_pass_two(operation):
            continue
        clock_advance_phases += _apply_state_operation(
            state,
            operation,
            aliases,
            warnings,
        )
    for operation in change_set.operations:
        if not _defer_to_pass_two(operation):
            continue
        clock_advance_phases += _apply_state_operation(
            state,
            operation,
            aliases,
            warnings,
        )

    if clock_advance_phases:
        state.world_clock = state.world_clock.advance_phase(clock_advance_phases)

    return StateApplyResult(
        state=CurrentState.model_validate(state.model_dump(mode="python")),
        warnings=warnings,
    )


def _defer_to_pass_two(operation: StateOperation) -> bool:
    """Operations that consume or modify an existing entity run in pass two."""
    if isinstance(operation, ParticipantAttitudeChange):
        return True
    if isinstance(operation, InventoryDelta):
        return operation.delta < 0
    if isinstance(operation, MoneyDelta):
        return operation.delta < 0
    return False


def _apply_state_operation(
    state: CurrentState,
    operation: StateOperation,
    aliases: dict[str, str],
    warnings: list[StateChangeWarning],
) -> int:
    """Apply a single operation; returns the number of clock phases advanced."""
    if isinstance(operation, LocationChange):
        if normalize_key(operation.location) == normalize_key(state.scene.location):
            _warn(
                warnings,
                operation,
                "location_no_change",
                f"location unchanged and ignored: {state.scene.location}",
                state.scene.location,
            )
            return 0
        state.scene.location = operation.location.strip()
        state.scene.scene_id = _increment_scene_id(state.scene.scene_id)
    elif isinstance(operation, ParticipantAdd):
        existing = _find_participant(state, operation.name, aliases)
        if existing is not None:
            _warn(
                warnings,
                operation,
                "participant_already_present",
                (
                    f"participant already present and ignored: {existing.name} "
                    "(location change does not clear participants)"
                ),
                existing.name,
            )
            return 0
        canonical_name = aliases.get(normalize_key(operation.name), operation.name.strip())
        state.scene.participants.append(ParticipantState(name=canonical_name))
    elif isinstance(operation, ParticipantRemove):
        participant = _find_participant(state, operation.name, aliases)
        if participant is None:
            _warn(
                warnings,
                operation,
                "participant_not_present",
                f"participant not present; removal ignored: {operation.name.strip()}",
                operation.name.strip(),
            )
            return 0
        if normalize_key(participant.name) == normalize_key(state.player.name):
            _warn(
                warnings,
                operation,
                "player_remove_forbidden",
                "the player cannot be removed; operation ignored",
                participant.name,
            )
            return 0
        state.scene.participants.remove(participant)
    elif isinstance(operation, ParticipantAttitudeChange):
        participant = _resolve_npc_attachment_target(
            state,
            operation,
            aliases,
            warnings,
        )
        if participant is None:
            return 0
        updated_attitude = _clean_optional(operation.attitude_to_player)
        if updated_attitude == participant.attitude_to_player:
            _warn(
                warnings,
                operation,
                "attitude_no_change",
                (
                    f"attitude unchanged and ignored: {participant.name} "
                    "already has this attitude_to_player"
                ),
                participant.name,
            )
            return 0
        participant.attitude_to_player = updated_attitude
    elif isinstance(operation, WorldClockAdvance):
        return operation.phases
    elif isinstance(operation, PlayerVisibleStatusChange):
        updated_status = _clean_optional(operation.visible_status)
        if updated_status == state.player.visible_status:
            _warn(
                warnings,
                operation,
                "visible_status_no_change",
                "player visible status unchanged and ignored",
                str(state.player.visible_status or ""),
            )
            return 0
        state.player.visible_status = updated_status
    elif isinstance(operation, InventoryDelta):
        _apply_inventory_delta(state, operation, warnings)
    elif isinstance(operation, MoneyDelta):
        _apply_money_delta(state, operation, warnings)
    elif isinstance(operation, FixedAssetAdd):
        _add_unique(
            state.resources.fixed_assets,
            operation.asset,
            operation,
            warnings,
            "fixed asset",
        )
    elif isinstance(operation, FixedAssetRemove):
        _remove_existing(
            state.resources.fixed_assets,
            operation.asset,
            operation,
            warnings,
            "fixed asset",
        )
    elif isinstance(operation, TemporaryAssetAdd):
        _add_unique(
            state.resources.temporary_assets,
            operation.asset,
            operation,
            warnings,
            "temporary asset",
        )
    elif isinstance(operation, TemporaryAssetRemove):
        _remove_existing(
            state.resources.temporary_assets,
            operation.asset,
            operation,
            warnings,
            "temporary asset",
        )
    return 0


def _apply_inventory_delta(
    state: CurrentState,
    operation: InventoryDelta,
    warnings: list[StateChangeWarning],
) -> None:
    item = next(
        (
            item
            for item in state.resources.inventory
            if normalize_key(item.name) == normalize_key(operation.item)
        ),
        None,
    )
    if item is None:
        if operation.delta < 0:
            _warn(
                warnings,
                operation,
                "inventory_item_missing",
                f"cannot remove missing inventory item; ignored: {operation.item}",
                operation.item,
            )
            return
        state.resources.inventory.append(
            InventoryItem(name=operation.item.strip(), quantity=operation.delta)
        )
        return

    quantity = item.quantity + operation.delta
    if quantity < 0:
        _warn(
            warnings,
            operation,
            "inventory_delta_exceeds",
            (
                f"inventory delta exceeds quantity; ignored: "
                f"{item.name} {item.quantity} + {operation.delta}"
            ),
            item.name,
        )
        return
    if quantity == 0:
        state.resources.inventory.remove(item)
    else:
        item.quantity = quantity


def _apply_money_delta(
    state: CurrentState,
    operation: MoneyDelta,
    warnings: list[StateChangeWarning],
) -> None:
    current = state.resources.money
    if current is None:
        if operation.delta < 0:
            _warn(
                warnings,
                operation,
                "money_balance_unknown",
                "cannot spend money when the balance is unknown; ignored",
                "",
            )
            return
        current = 0
    updated = current + operation.delta
    if updated < 0:
        _warn(
            warnings,
            operation,
            "money_negative_balance",
            (
                "money delta would create a negative balance; ignored: "
                f"{updated}"
            ),
            "",
        )
        return
    state.resources.money = updated


def _find_participant(
    state: CurrentState,
    name: str,
    aliases: dict[str, str],
) -> ParticipantState | None:
    target = normalize_key(aliases.get(normalize_key(name), name))
    for participant in state.scene.participants:
        canonical = normalize_key(
            aliases.get(normalize_key(participant.name), participant.name)
        )
        if canonical == target:
            return participant
    return None


def _resolve_npc_attachment_target(
    state: CurrentState,
    operation: ParticipantAttitudeChange,
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
    if normalize_key(participant.name) == normalize_key(state.player.name):
        _warn(
            warnings,
            operation,
            "attitude_target_player",
            f"participant attitude cannot target the player; ignored: {participant.name}",
            participant.name,
        )
        return None
    return participant


def _alias_map(npc_aliases: dict[str, list[str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, alias_values in npc_aliases.items():
        canonical_key = normalize_key(name)
        canonical_name = str(name).strip()
        if not canonical_key:
            continue
        result[canonical_key] = canonical_name
        for alias in alias_values:
            if normalize_key(alias):
                result[normalize_key(alias)] = canonical_name
    return result


def _warn(
    warnings: list[StateChangeWarning],
    operation: StateOperation,
    code: str,
    message: str,
    target: str,
) -> None:
    warnings.append(StateChangeWarning(
        operation_id=operation.operation_id,
        operation_type=operation.type,
        target=target,
        code=code,
        message=message,
    ))


def _add_unique(
    values: list[str],
    value: str,
    operation: StateOperation,
    warnings: list[StateChangeWarning],
    label: str,
) -> None:
    clean = value.strip()
    if any(normalize_key(existing) == normalize_key(clean) for existing in values):
        _warn(
            warnings,
            operation,
            "asset_already_exists",
            f"{label} already exists and ignored: {clean}",
            clean,
        )
        return
    values.append(clean)


def _remove_existing(
    values: list[str],
    value: str,
    operation: StateOperation,
    warnings: list[StateChangeWarning],
    label: str,
) -> None:
    existing = next((item for item in values if normalize_key(item) == normalize_key(value)), None)
    if existing is None:
        _warn(
            warnings,
            operation,
            "asset_not_found",
            f"cannot remove missing {label}; ignored: {value}",
            str(value),
        )
        return
    values.remove(existing)


def _clean_optional(value: str | None) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _increment_scene_id(scene_id: str) -> str:
    match = re.search(r"(\d+)$", scene_id)
    if match:
        number = int(match.group(1)) + 1
        return scene_id[: match.start()] + str(number).zfill(len(match.group(1)))
    return scene_id + "_1"
