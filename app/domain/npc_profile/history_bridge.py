"""Trusted field-log evidence pointers for Context Facts retrieval.

This module owns the retrieval-side contract for evidence-backed NPC field
changes: a read-only ``NPCFieldHistoryLink`` that mirrors the Continuity
lifecycle link shape.  The ``NPCProfileHistoryBridge`` class that produces
these links arrives in a later phase; this module defines the link model so the
shared relation admission seam can be fixed and tested independently of the
bridge's eligibility logic.

It deliberately does not import anything from ``context_facts``: normalization
into the internal ``RelationEvidenceLink`` happens in the adapter layer, and
the domain store schema stays free of reverse dependencies.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.npc_profile.field_history import (
    NPCFieldHistoryName,
)


NPCFieldRelationReason = Literal[
    "npc_name_match",
    "npc_alias_match",
    "npc_delta_topic_match",
    "npc_value_topic_match",
]


class NPCFieldHistoryLink(BaseModel):
    """A structural pointer from one accepted field log into Confirmed History.

    ``evidence_basis_primary`` is the node-specific semantic basis (the
    ``summary_delta`` for ``public_summary``, the ``new_value`` otherwise);
    ``old_value`` never enters the basis so a later aggregate cannot broadcast
    onto an older node.  Pointer integrity is verified by the shared relation
    admission pass, not here.
    """

    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    field_name: NPCFieldHistoryName
    log_id: int = Field(ge=1)
    turn_index: int = Field(ge=0)
    evidence_id: str = Field(min_length=1)
    evidence_hash: str | None = Field(default=None, min_length=64, max_length=64)
    evidence_position: int = Field(ge=0)
    triggering_intent_ids: list[str] = Field(min_length=1)
    match_reasons: list[NPCFieldRelationReason] = Field(min_length=1)
    evidence_basis_primary: str | None = Field(default=None, max_length=512)
    evidence_basis_context: str | None = Field(default=None, max_length=900)
    basis_complete: bool = True
    source: Literal["npc_profile"] = "npc_profile"
