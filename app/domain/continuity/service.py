from __future__ import annotations

from app.domain.confirmed_history import (
    HistoryDocument,
    history_document_text_hash,
)

from .projector import ContinuityProjector
from .schemas import (
    ContinuityApplyResult,
    ContinuityItem,
    ContinuityProjection,
)
from .store import ContinuityStore


class ContinuityService:
    def __init__(
        self,
        projector: ContinuityProjector | None,
        store: ContinuityStore,
    ):
        self.projector = projector
        self.store = store

    def project(
        self,
        *,
        current_documents: list[HistoryDocument],
        trace_id: str | None = None,
    ) -> ContinuityProjection:
        if self.projector is None:
            raise RuntimeError("continuity projector is not configured")
        return self.projector.project(
            current_documents=current_documents,
            active_items=self.store.list_active(),
            trace_id=trace_id,
        )

    def apply(
        self,
        projection: ContinuityProjection,
        *,
        turn_index: int,
        evidence_documents: dict[str, HistoryDocument],
    ) -> ContinuityApplyResult:
        if self.store.turn_processed(turn_index):
            return ContinuityApplyResult()

        referenced_item_ids = [
            item_id
            for operation in projection.operations
            if (item_id := getattr(operation, "item_id", None)) is not None
        ]
        if len(referenced_item_ids) != len(set(referenced_item_ids)):
            raise ValueError(
                "continuity item may be changed only once per turn"
            )

        result = ContinuityApplyResult()
        with self.store.transaction():
            for operation in projection.operations:
                unknown_evidence = (
                    set(operation.evidence_ids) - set(evidence_documents)
                )
                if unknown_evidence:
                    raise ValueError(
                        "unknown continuity evidence IDs: "
                        + ", ".join(sorted(unknown_evidence))
                    )
                invalid_evidence = [
                    evidence_id
                    for evidence_id in operation.evidence_ids
                    if (
                        evidence_documents[evidence_id].source_type
                        != "scene_paragraph"
                        or evidence_documents[evidence_id].turn_index
                        != turn_index
                        or evidence_documents[evidence_id].document_id
                        != evidence_id
                    )
                ]
                if invalid_evidence:
                    raise ValueError(
                        "continuity evidence must be the identified scene "
                        f"paragraph from turn {turn_index}: "
                        + ", ".join(sorted(invalid_evidence))
                    )

                if operation.operation == "create":
                    duplicate = self.store.find_duplicate(operation.item)
                    if duplicate is not None:
                        result.duplicate_creates_skipped += 1
                        continue
                    item = ContinuityItem(
                        **operation.item.model_dump(),
                        current_state=None,
                        created_turn=turn_index,
                        updated_turn=turn_index,
                    )
                    self.store.insert(item)
                    item_id = item.id
                    result.created_ids.append(item_id)
                elif operation.operation == "update":
                    self.store.update_active(
                        operation.item_id,
                        operation.to_draft(),
                        turn_index=turn_index,
                    )
                    item_id = operation.item_id
                    result.updated_ids.append(item_id)
                else:
                    self.store.close_item(
                        operation.item_id,
                        status=(
                            "resolved"
                            if operation.operation == "resolve"
                            else "cancelled"
                        ),
                        turn_index=turn_index,
                    )
                    item_id = operation.item_id
                    getattr(
                        result,
                        (
                            "resolved_ids"
                            if operation.operation == "resolve"
                            else "cancelled_ids"
                        ),
                    ).append(item_id)

                self.store.add_lifecycle_log(
                    item_id=item_id,
                    operation=operation.operation,
                    turn_index=turn_index,
                    state_delta=(
                        operation.state_delta
                        if operation.operation == "update"
                        else None
                    ),
                    reason=operation.reason,
                    evidence=[
                        (
                            evidence_id,
                            history_document_text_hash(
                                evidence_documents[evidence_id]
                            ),
                        )
                        for evidence_id in operation.evidence_ids
                    ],
                )
            self.store.mark_turn_processed(turn_index)
        return result
