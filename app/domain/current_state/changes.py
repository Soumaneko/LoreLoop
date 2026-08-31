from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.text import normalize_key
from .schemas import (
    CurrentState,
    FinalSceneFrame,
    InventoryItem,
    ParticipantState,
    PHASE_ORDER,
)


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
    evidence_fact_ids: list[str] = Field(default_factory=list)
    evidence_paragraph_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _requires_evidence(self):
        if not (self.evidence_fact_ids or self.evidence_paragraph_ids):
            raise ValueError(
                "operation requires at least one evidence_fact_id or "
                "evidence_paragraph_id"
            )
        return self


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
    # No total cap here: only the model output is bounded (30 items in the
    # updater validator); deterministic frame-compiled operations scale with
    # the participant count and are never truncated.
    operations: list[StateOperation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _operation_ids_must_be_unique(self):
        ids = [operation.operation_id for operation in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("operation_id values must be unique")
        return self


#: 由 ``FinalSceneFrame`` 经确定性引擎编译的操作类型；LLM 不得直接输出。
FRAME_OPERATION_TYPES: frozenset[str] = frozenset({
    "location_change",
    "participant_add",
    "participant_remove",
    "world_clock_advance",
})

#: StateUpdater LLM 输出中允许的剩余操作类型（态度、外观、资源与资产）。
RESOLUTION_OPERATION_TYPES: frozenset[str] = frozenset({
    "participant_attitude_change",
    "player_visible_status_change",
    "inventory_delta",
    "money_delta",
    "fixed_asset_add",
    "fixed_asset_remove",
    "temporary_asset_add",
    "temporary_asset_remove",
})


class StateResolutionOutput(BaseModel):
    """LLM 输出契约：简短时空审计 + 落幕终态定格 + 剩余资源操作。"""

    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1)
    frame: FinalSceneFrame
    operations: list[StateOperation] = Field(default_factory=list, max_length=30)


class StateResolutionOutputLoose(BaseModel):
    """顶层宽松容器：frame 与 operations 的逐项校验由 output_validator 负责。"""

    model_config = ConfigDict(extra="allow")

    decision: str | None = None
    frame: Any = None
    operations: list[Any] = Field(default_factory=list)


def calculate_phase_advance(
    current_phase: str,
    day_offset: int,
    target_phase: str | None,
) -> int | None:
    """Deterministically resolve the clock step count from the final frame.

    Returns ``None`` when the inputs cannot form a non-negative advance; the
    caller records a warning and keeps the clock unchanged.
    """

    if current_phase not in PHASE_ORDER:
        return None
    steps = day_offset * len(PHASE_ORDER)
    if target_phase is not None:
        if target_phase not in PHASE_ORDER:
            return None
        steps += PHASE_ORDER.index(target_phase) - PHASE_ORDER.index(current_phase)
    elif day_offset > 0:
        # Whole days elapsed without a declared end phase: keep the same
        # time of day on the new day.
        pass
    return steps if steps >= 0 else None


def compile_frame_operations(
    current_state: CurrentState,
    frame: FinalSceneFrame,
    *,
    npc_aliases: dict[str, list[str]] | None = None,
) -> tuple[list[StateOperation], list[StateChangeWarning]]:
    """Deterministic diff engine compiling a FinalSceneFrame.

    Compiles the scene-ending snapshot into auditable atomic StateOperations:
    relocation, the relocation eviction guard (participants absent from the
    final frame do not follow the protagonist), explicit departures, new
    arrivals, and the absolute clock advance. Names are normalized through the
    alias map; the player is never added or removed; an NPC listed as both
    present and departed counts as present. The emitted operation/warning
    contract matches the ``StateChangeSet`` audit format.
    """

    warnings: list[StateChangeWarning] = []
    evidence_fact_ids = list(frame.evidence_fact_ids)
    evidence_paragraph_ids = list(frame.evidence_paragraph_ids)
    reason = "由落幕终态定格（FinalSceneFrame）确定性编译"
    operations: list[StateOperation] = []

    def _warning(code: str, message: str, target: str) -> None:
        warnings.append(StateChangeWarning(
            operation_id="frame",
            operation_type="final_scene_frame",
            target=target,
            code=code,
            message=message,
        ))

    final_location = frame.final_location
    location_changed = bool(
        final_location
        and normalize_key(final_location)
        != normalize_key(current_state.scene.location)
    )
    if final_location and location_changed:
        operations.append(LocationChange(
            operation_id="frame_location",
            reason=reason,
            evidence_fact_ids=evidence_fact_ids,
            evidence_paragraph_ids=evidence_paragraph_ids,
            type="location_change",
            location=final_location,
        ))

    phases = calculate_phase_advance(
        current_state.world_clock.phase,
        frame.day_offset,
        frame.final_phase,
    )
    if phases is None and (frame.day_offset or frame.final_phase):
        _warning(
            "frame_time_ignored",
            (
                "frame time fields cannot form a non-negative advance "
                f"(day_offset={frame.day_offset}, "
                f"final_phase={frame.final_phase}); clock unchanged"
            ),
            "",
        )
    elif phases:
        operations.append(WorldClockAdvance(
            operation_id="frame_clock",
            reason=reason,
            evidence_fact_ids=evidence_fact_ids,
            evidence_paragraph_ids=evidence_paragraph_ids,
            type="world_clock_advance",
            phases=phases,
        ))

    aliases = _alias_map(npc_aliases or {})
    player_key = normalize_key(current_state.player.name)
    old_participants = [
        participant for participant in current_state.scene.participants
        if normalize_key(participant.name) != player_key
    ]
    old_names = {
        normalize_key(participant.name): participant.name
        for participant in old_participants
    }

    def _canonical(name: str) -> str:
        return aliases.get(normalize_key(name), name.strip())

    present_keys: set[str] = set()
    for index, name in enumerate(frame.active_present_npcs, start=1):
        canonical = _canonical(name)
        key = normalize_key(canonical)
        if key == player_key or key in present_keys:
            # The player is never a tracked participant; duplicate canonical
            # names (e.g. alias variants) are admitted once.
            continue
        present_keys.add(key)
        matched = next(
            (existing for existing in old_names if existing == key),
            None,
        )
        if matched is not None:
            continue
        operations.append(ParticipantAdd(
            operation_id=f"frame_present_{index}",
            reason=reason,
            evidence_fact_ids=evidence_fact_ids,
            evidence_paragraph_ids=evidence_paragraph_ids,
            type="participant_add",
            name=canonical,
        ))

    departed_seen: set[str] = set()
    for name in frame.departed_npcs:
        key = normalize_key(_canonical(name))
        if key == player_key or key in departed_seen:
            continue
        if key in present_keys:
            # Conflict resolution: the final presence snapshot wins; an NPC
            # listed as both present and departed counts as present.
            continue
        departed_seen.add(key)
        if key in old_names:
            operations.append(ParticipantRemove(
                operation_id=f"frame_departed_{len(departed_seen)}",
                reason=reason,
                evidence_fact_ids=evidence_fact_ids,
                evidence_paragraph_ids=evidence_paragraph_ids,
                type="participant_remove",
                name=_canonical(name),
            ))
    # Relocation default-eviction: participants absent from the final frame
    # do not follow the protagonist to a new location.
    if location_changed:
        for key, name in old_names.items():
            if key in present_keys or key in departed_seen:
                continue
            operations.append(ParticipantRemove(
                operation_id=f"frame_evicted_{len(operations)}",
                reason=(
                    "地点变更且未出现在落幕在场名单，默认不跟随"
                ),
                evidence_fact_ids=evidence_fact_ids,
                evidence_paragraph_ids=evidence_paragraph_ids,
                type="participant_remove",
                name=name,
            ))
    return operations, warnings


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
