"""Evidence-backed history for accepted NPC stable-field changes.

This module owns the durable contract between NPC batch admission and the
future History bridge.  It deliberately does not perform retrieval: it only
projects already-accepted scalar changes onto exact Confirmed History
paragraph pointers and computes a deterministic replay hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.confirmed_history import history_document_text_hash
from app.domain.confirmed_history.schemas import HistoryDocument

from .changes import AcceptedNPCFieldChange, AcceptedNPCScalarChange


#: 可读的字段名集合；``public_summary`` 仅为 v7 遗留只读 audit row 保留，
#: 不在新写入集合内。
NPCFieldHistoryName = Literal[
    "public_summary",
    "role",
    "appearance",
    "faction",
    "last_scene_summary",
]

#: 新写入集合：只有这些 proposal 稳定字段会产生 field-history node。
BRIDGEABLE_NPC_FIELD_NAMES: frozenset[str] = frozenset(
    {"last_scene_summary", "role", "appearance", "faction"}
)


class NPCFieldChangeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_id: int = Field(ge=1)
    position: int = Field(ge=0)
    evidence_id: str = Field(min_length=1)
    evidence_hash: str = Field(min_length=64, max_length=64)


class NPCFieldChangeLog(BaseModel):
    """One persisted field-log row as read back from the store.

    ``change_basis`` is deliberately nullable here: v8->v9 migration leaves
    legacy rows with ``NULL`` (no basis), and the History bridge marks those
    ``basis_complete=False`` (incomplete, degraded rescue only).  The write API
    never produces ``NULL`` for accumulating fields -- that contract lives on
    ``PreparedNPCFieldChange`` -- so a NULL in the read model always means
    "migrated legacy row", never "written without a basis".
    """

    model_config = ConfigDict(extra="forbid")

    log_id: int = Field(ge=1)
    npc_id: str = Field(min_length=1)
    field_name: NPCFieldHistoryName
    turn_index: int = Field(ge=0)
    old_value: str | None = None
    new_value: str = Field(min_length=1)
    change_basis: str | None = None
    summary_delta: str | None = None
    source: Literal["proposal"] = "proposal"
    evidence: list[NPCFieldChangeEvidence] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_node(self):
        _validate_summary_delta(self.field_name, self.summary_delta)
        _validate_change_basis(self.field_name, self.change_basis)
        positions = [item.position for item in self.evidence]
        if positions != list(range(len(self.evidence))):
            raise ValueError("NPC field evidence positions must be contiguous")
        if any(item.log_id != self.log_id for item in self.evidence):
            raise ValueError("NPC field evidence belongs to another log")
        return self


class NPCFieldChangeRecordResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=0)
    batch_hash: str = Field(min_length=64, max_length=64)
    logs: list[NPCFieldChangeLog] = Field(default_factory=list)
    replayed: bool = False
    reset_performed: bool = False


class PreparedNPCFieldEvidence(BaseModel):
    """Validated evidence pointer before its database log_id is allocated."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0)
    evidence_id: str = Field(min_length=1)
    evidence_hash: str = Field(min_length=64, max_length=64)


class PreparedNPCFieldChange(BaseModel):
    """One bridgeable accepted change ready for atomic persistence.

    This is the write-side contract: the batch field updater must persist a
    non-empty ``change_basis`` (at most 120 characters) for every accumulating
    stable field change.  ``None`` is only legal for ``last_scene_summary``,
    whose whole new value is its basis.  Rejecting ``None`` here makes it
    impossible for the current write API to fabricate a pseudo-legacy log with
    a NULL basis.
    """

    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    field_name: NPCFieldHistoryName
    turn_index: int = Field(ge=0)
    old_value: str | None = None
    new_value: str = Field(min_length=1)
    change_basis: str | None = Field(default=None, min_length=1, max_length=120)
    summary_delta: str | None = None
    source: Literal["proposal"] = "proposal"
    evidence: list[PreparedNPCFieldEvidence] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_node(self):
        _validate_summary_delta(self.field_name, self.summary_delta)
        _validate_change_basis(self.field_name, self.change_basis)
        if self.field_name in ("role", "appearance", "faction"):
            if not self.change_basis or not self.change_basis.strip():
                raise ValueError(
                    "role/appearance/faction field logs require a non-empty "
                    "change_basis"
                )
        positions = [item.position for item in self.evidence]
        if positions != list(range(len(self.evidence))):
            raise ValueError("NPC field evidence positions must be contiguous")
        return self


class PreparedNPCFieldBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=0)
    scene_id: str = Field(min_length=1)
    batch_hash: str = Field(min_length=64, max_length=64)
    changes: list[PreparedNPCFieldChange] = Field(default_factory=list)


def prepare_accepted_field_changes(
    *,
    changes: Sequence[AcceptedNPCFieldChange],
    evidence_documents: Iterable[HistoryDocument],
    turn_index: int,
    scene_id: str,
) -> PreparedNPCFieldBatch:
    """Filter and validate the stable proposal changes for one confirmed turn.

    Eligibility is intentionally narrow.  CurrentState sync, current intent,
    aliases, abilities and future mutation variants do not silently become
    field-history nodes.  Once a change is eligible, every pointer is strict:
    one unknown/wrong-turn/wrong-scene/non-paragraph/duplicate evidence ID
    rejects the whole prepared batch.
    """

    if turn_index < 0:
        raise ValueError("turn_index must be >= 0")
    clean_scene_id = str(scene_id).strip()
    if not clean_scene_id:
        raise ValueError("scene_id must not be blank")

    documents: dict[str, HistoryDocument] = {}
    for document in evidence_documents:
        if document.document_id in documents:
            raise ValueError(
                f"duplicate HistoryDocument id: {document.document_id!r}"
            )
        documents[document.document_id] = document

    eligible: list[AcceptedNPCScalarChange] = []
    for change in changes:
        if not isinstance(change, AcceptedNPCScalarChange):
            continue
        if (
            change.source != "proposal"
            or change.field not in BRIDGEABLE_NPC_FIELD_NAMES
        ):
            continue
        if change.turn_index != turn_index:
            raise ValueError(
                "eligible NPC field change turn does not match record turn: "
                f"{change.npc_id}/{change.field}"
            )
        eligible.append(change)

    node_keys = [(item.npc_id, item.field, item.turn_index) for item in eligible]
    if len(node_keys) != len(set(node_keys)):
        raise ValueError(
            "duplicate NPC field-change node in one batch "
            "(npc_id, field, turn_index)"
        )

    prepared: list[PreparedNPCFieldChange] = []
    for change in eligible:
        if len(change.evidence_ids) != len(set(change.evidence_ids)):
            raise ValueError(
                f"duplicate evidence ids for {change.npc_id}/{change.field}"
            )
        pointers: list[PreparedNPCFieldEvidence] = []
        for position, evidence_id in enumerate(change.evidence_ids):
            document = documents.get(evidence_id)
            if document is None:
                raise ValueError(f"unknown HistoryDocument id: {evidence_id!r}")
            if document.source_type != "scene_paragraph":
                raise ValueError(
                    f"NPC field evidence must be a scene paragraph: {evidence_id!r}"
                )
            if document.turn_index != turn_index:
                raise ValueError(
                    f"NPC field evidence belongs to another turn: {evidence_id!r}"
                )
            if document.scene_id != clean_scene_id:
                raise ValueError(
                    f"NPC field evidence belongs to another scene: {evidence_id!r}"
                )
            pointers.append(PreparedNPCFieldEvidence(
                position=position,
                evidence_id=evidence_id,
                evidence_hash=history_document_text_hash(document),
            ))
        prepared.append(PreparedNPCFieldChange(
            npc_id=change.npc_id,
            field_name=cast(NPCFieldHistoryName, change.field),
            turn_index=turn_index,
            old_value=change.old_value,
            new_value=change.new_value,
            change_basis=change.change_basis,
            summary_delta=None,
            source="proposal",
            evidence=pointers,
        ))

    payload = {
        "turn_index": turn_index,
        "scene_id": clean_scene_id,
        "changes": [
            change.model_dump(mode="json")
            for change in prepared
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return PreparedNPCFieldBatch(
        turn_index=turn_index,
        scene_id=clean_scene_id,
        batch_hash=hashlib.sha256(encoded).hexdigest(),
        changes=prepared,
    )


def _validate_summary_delta(
    field_name: NPCFieldHistoryName,
    summary_delta: str | None,
) -> None:
    if field_name == "public_summary":
        if not summary_delta or not summary_delta.strip():
            raise ValueError("public_summary field logs require summary_delta")
    elif summary_delta is not None:
        raise ValueError("summary_delta is only valid for public_summary logs")


def _validate_change_basis(
    field_name: NPCFieldHistoryName,
    change_basis: str | None,
) -> None:
    if change_basis is None:
        return
    if field_name not in ("role", "appearance", "faction"):
        raise ValueError(
            "change_basis is only valid for accumulating stable fields "
            f"(role/appearance/faction), got {field_name}"
        )
