from __future__ import annotations

from concurrent.futures import Future
from typing import Any, Callable, Mapping, TypedDict, TypeVar, cast

from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.continuity import ContinuityProjection
from app.domain.current_state.changes import StateChangeSet, StateChangeWarning
from app.domain.current_state.schemas import CurrentState
from app.domain.director import DirectorDecisionBatch, DirectorSkillExecutionBatch
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.scene_summary.schemas import SceneSummary

from .npc_result import NpcTurnNodeResult

T = TypeVar("T")


# The complete confirmation cache slot registry. Every key a confirmation
# writes is classified here once; the admission and rollback policies derive
# from these categories instead of hand-maintained key lists.
#
# - ``result``: pure LLM/derived confirmation results, always admitted and
#   always kept on rollback (they cost tokens to redo).
# - ``write_result``: results whose production touched DB state, admitted only
#   for real confirmations and discarded on rollback.
# - ``preload``: preparer-owned values, never admitted from a graph node and
#   rolled back with the write scope.
# - ``marker``: boolean completion guards advanced by the committer/scheduler.
# - ``marker_data``: non-boolean values attached to a completion marker.
_SLOT_CATEGORIES: dict[str, frozenset[str]] = {
    "result": frozenset({
        "scene_facts",
        "updated_state",
        "state_change_set",
        "state_update_warnings",
        "turn_summary",
        "action_preview",
        "continuity_projection",
    }),
    "write_result": frozenset({
        "npc_turn_result",
        "director_decision",
        "director_executions",
        "director_event_reviews",
        "recorded_setting_ids",
    }),
    "preload": frozenset({
        "director_npc_profile_ids",
        "confirmed_director_npc_drafts",
        "existing_npc_profiles",
    }),
    "marker": frozenset({
        "state_saved",
        "turn_summary_saved",
        "short_term_saved",
        "confirmed_history_saved",
        "npc_field_logs_saved",
        "scene_summary_scheduled",
    }),
    "marker_data": frozenset({
        "turn_summary_id",
        "scene_summary_future",
        "scene_summary_job_id",
        "scene_summary_operation_id",
    }),
}

# Scene-text recovery fingerprint. A change invalidates the whole cache.
_KEY_SLOT = "key"

# Every slot a graph node may populate into the shared cache, keyed by whether
# the node result is write-scoped.
_ALWAYS_ADMITTED = _SLOT_CATEGORIES["result"]
_WRITE_ADMITTED = _SLOT_CATEGORIES["write_result"]

# The slots a rollback reset keeps: pure results plus the recovery fingerprint.
_ROLLBACK_SAFE = _SLOT_CATEGORIES["result"] | {_KEY_SLOT}

# Slots a caller may write through set_result/compute_if_missing/atomic.
_WRITABLE_SLOTS = frozenset().union(*(
    _SLOT_CATEGORIES[name]
    for name in ("result", "write_result", "preload", "marker_data")
))

# The completion-guard category enforced by completed/mark_completed.
_MARKER_SLOTS = _SLOT_CATEGORIES["marker"]


class ConfirmationCacheData(TypedDict, total=False):
    """Backing data of the confirmation retry/recovery cache.

    The single static contract for the raw dict carried on a pending and in
    ConfirmationState; the runtime ConfirmationCache wraps an instance of it.
    """

    key: str
    scene_facts: SceneFactSet
    updated_state: CurrentState
    state_change_set: StateChangeSet
    state_update_warnings: list[StateChangeWarning]
    turn_summary: SceneSummary
    action_preview: ActionPreviewResult
    director_decision: DirectorDecisionBatch
    director_executions: DirectorSkillExecutionBatch
    director_event_reviews: list[dict[str, Any]]
    npc_turn_result: NpcTurnNodeResult
    existing_npc_profiles: list[NPCProfile]
    director_npc_profile_ids: list[str]
    confirmed_director_npc_drafts: list[DirectorNPCDraft]
    turn_summary_saved: bool
    turn_summary_id: str
    state_saved: bool
    short_term_saved: bool
    confirmed_history_saved: bool
    npc_field_logs_saved: bool
    scene_summary_future: Future[Any]
    scene_summary_job_id: str | None
    scene_summary_operation_id: str | None
    scene_summary_scheduled: bool
    continuity_projection: ContinuityProjection
    recorded_setting_ids: list[str]


class ConfirmationCache:
    """Own the confirmation retry/recovery cache over a pending's raw dict.

    Centralizes result lifecycle, write scope, rollback safety, completion
    guards, and the scene-text recovery fingerprint. Graph nodes and lifecycle
    modules describe which slot they fill without sharing the raw key policy;
    the registry above is the single definition of what survives a retry and
    what a rollback discards.
    """

    def __init__(self, data: Mapping[str, Any]) -> None:
        # The backing store is a mutable dict at runtime (a pending's
        # confirmation_cache); Mapping keeps both plain dicts and the state's
        # cache TypedDict assignable here.
        self._data = cast("dict[str, Any]", data)

    @staticmethod
    def _require_writable(slot: str) -> None:
        if slot not in _WRITABLE_SLOTS:
            raise ValueError(
                f"unknown confirmation cache slot {slot!r}; "
                "register it in _SLOT_CATEGORIES before writing"
            )

    @staticmethod
    def _require_marker(marker: str) -> None:
        if marker not in _MARKER_SLOTS:
            raise ValueError(
                f"{marker!r} is not a confirmation completion marker; "
                "register it in the marker category of _SLOT_CATEGORIES"
            )

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    # -- result lifecycle -------------------------------------------------

    def produced(self, slot: str) -> bool:
        """True when the slot is present, even if its value is empty."""
        return slot in self._data

    def result(self, slot: str) -> Any:
        """The cached value, or None when the slot was never produced."""
        return self._data.get(slot)

    def set_result(self, slot: str, value: Any) -> None:
        self._require_writable(slot)
        self._data[slot] = value

    def compute_if_missing(self, slot: str, factory: Callable[[], T]) -> T:
        """Return the cached result or compute, store, and return it."""
        self._require_writable(slot)
        if slot in self._data:
            return self._data[slot]
        value = factory()
        self._data[slot] = value
        return value

    def atomic(
        self,
        *slots: str,
        factory: Callable[[], tuple[Any, ...]],
    ) -> tuple[Any, ...]:
        """Return a cached result tuple or recompute every slot together.

        A partial cache is treated as missing so an interrupted multi-slot
        result is never reused half-written. The factory's return arity must
        match the slots; a mismatch raises before any cache write so a bad
        factory can never leave a half-written cache behind.
        """
        if not slots:
            raise ValueError("atomic() requires at least one cache slot")
        if len(set(slots)) != len(slots):
            raise ValueError(
                f"atomic() slots must be unique: {slots!r}"
            )
        for slot in slots:
            self._require_writable(slot)
        if any(slot not in self._data for slot in slots):
            values = tuple(factory())
            if len(values) != len(slots):
                raise ValueError(
                    "atomic() factory returned "
                    f"{len(values)} values for {len(slots)} slots: {slots!r}"
                )
            for slot, value in zip(slots, values):
                self._data[slot] = value
            return values
        return tuple(self._data[slot] for slot in slots)

    # -- write scope / admission ------------------------------------------

    def admit(
        self,
        node_update: Mapping[str, Any],
        *,
        write_db: bool,
    ) -> None:
        """Fold a graph node's cached results into the shared cache.

        Only registered result slots are admitted; write-scoped results are
        admitted exclusively for real confirmations so a dry run never persists
        results that touched DB state.
        """
        admitted = _ALWAYS_ADMITTED
        if write_db:
            admitted = admitted | _WRITE_ADMITTED
        for slot in admitted & node_update.keys():
            self._data[slot] = node_update[slot]

    # -- rollback safety ---------------------------------------------------

    def reset_for_rollback(self) -> None:
        """Discard every write-scoped entry, keeping pure results and key."""
        retained = {
            slot: self._data[slot]
            for slot in _ROLLBACK_SAFE
            if slot in self._data
        }
        self._data.clear()
        self._data.update(retained)

    # -- recovery validity -------------------------------------------------

    def begin(self, scene_text: str) -> None:
        """Invalidate result and marker slots when scene text changed, retaining preload."""
        if self._data.get(_KEY_SLOT) != scene_text:
            retained_preload = {
                slot: self._data[slot]
                for slot in _SLOT_CATEGORIES["preload"]
                if slot in self._data
            }
            self._data.clear()
            self._data.update(retained_preload)
            self._data[_KEY_SLOT] = scene_text

    # -- completion guards -------------------------------------------------

    def completed(self, marker: str) -> bool:
        self._require_marker(marker)
        return bool(self._data.get(marker))

    def mark_completed(self, marker: str) -> None:
        self._require_marker(marker)
        self._data[marker] = True
