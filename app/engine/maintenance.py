import traceback
import uuid

from app.core.llm.config import create_gateway
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState
from app.domain.recall_memory.batch_classifier import MemoryBatchClassifier
from app.domain.recall_memory.consolidator import MemoryConsolidator
from app.domain.scene_summary.summarizer import SceneSummarizer
from app.workflows.consolidate_memories import consolidate_memories_workflow
from app.workflows.extract_memory import batch_classify_pending_memories_workflow
from app.workflows.scene_summaries import run_scene_summary_maintenance as run_scene_summary_maintenance_workflow

from .serialization import model_data
from .stores import (
    core_memory_store,
    current_state_store,
    entity_store,
    memory_store,
    pending_memory_store,
    scene_summary_store,
)


def run_scene_summary_maintenance(
    scene_text: str,
    current_state: CurrentState,
    scene_id: str,
    turn_index: int,
    trace_id: str,
    *,
    core_memory: CoreMemory,
) -> None:
    try:
        gateway = create_gateway()
        gateway.set_trace_context(
            turn_index=turn_index,
            session_type="maintenance",
        )
        summarizer = SceneSummarizer(gateway)
        with scene_summary_store() as summary_store:
            result = run_scene_summary_maintenance_workflow(
                scene_text=scene_text,
                current_state=current_state,
                core_memory=core_memory,
                scene_id=scene_id,
                turn_index=turn_index,
                summarizer=summarizer,
                store=summary_store,
                threshold=10,
                trace_id=trace_id,
            )
        print(
            "[debug-server] scene summary maintenance "
            f"l1={result.saved_l1_id} condensed={len(result.condensed_ids)}"
        )
    except Exception:
        print("[debug-server] scene summary maintenance failed")
        traceback.print_exc()


def run_memory_batch_maintenance(
    current_state: CurrentState,
    trace_id: str,
    turn_index: int,
    *,
    core_memory: CoreMemory,
) -> None:
    try:
        gateway = create_gateway()
        gateway.set_trace_context(
            turn_index=turn_index,
            session_type="maintenance",
        )
        classifier = MemoryBatchClassifier(gateway)
        with (
            pending_memory_store() as pending_store,
            memory_store() as mem_store,
            entity_store() as ent_store,
        ):
            result = batch_classify_pending_memories_workflow(
                current_state=current_state,
                core_memory=core_memory,
                classifier=classifier,
                pending_store=pending_store,
                memory_store=mem_store,
                entity_store=ent_store,
                turn_threshold=5,
                trace_id=trace_id,
            )
            consolidation_result = None
            if result.triggered and result.should_consolidate:
                consolidation_result = consolidate_memories_workflow(
                    store=mem_store,
                    consolidator=MemoryConsolidator(gateway),
                    core_memory=core_memory,
                    max_group_size=5,
                    trace_id=trace_id,
                )
        print(
            "[debug-server] memory batch maintenance "
            f"triggered={result.triggered} pending={result.pending_count} "
            f"saved={len(result.saved_ids)} classified={len(result.classified_pending_ids)} "
            f"batch_count={result.batch_classify_count}"
        )
        if consolidation_result is not None:
            print(
                "[debug-server] memory consolidation "
                f"groups={len(consolidation_result.groups)} "
                f"merged={consolidation_result.merged_count} "
                f"deactivated={consolidation_result.deactivated_count}"
            )
    except Exception:
        print("[debug-server] memory batch maintenance failed")
        traceback.print_exc()


def batch_classify_stream():
    trace_id = str(uuid.uuid4())
    force = True

    with current_state_store() as state_store:
        current_state = state_store.load_or_default()

    core_memory = core_memory_store().load_required()

    with pending_memory_store() as pending_store:
        pending_items = pending_store.list_unclassified()
        turn_count = pending_store.unclassified_turn_count()
        pending_total = pending_store.count()

    yield {
        "step": "batch_classify_started",
        "data": {
            "trace_id": trace_id,
            "pending_count": len(pending_items),
            "unclassified_turn_count": turn_count,
            "pending_total": pending_total,
        },
    }

    if not pending_items:
        yield {"step": "batch_classify_done", "data": {"reason": "No pending memories need classification."}}
        return

    yield {"step": "batch_classifying", "data": {}}

    gateway = create_gateway()
    gateway.set_trace_context(
        turn_index=max(
            (item.turn_index for item in pending_items),
            default=0,
        ),
        session_type="maintenance",
    )
    classifier = MemoryBatchClassifier(gateway)
    with (
        pending_memory_store() as bpstore,
        memory_store() as bmstore,
        entity_store() as ent_store,
    ):
        result = batch_classify_pending_memories_workflow(
            current_state=current_state,
            core_memory=core_memory,
            classifier=classifier,
            pending_store=bpstore,
            memory_store=bmstore,
            entity_store=ent_store,
            turn_threshold=1,
            force=force,
            trace_id=trace_id,
        )
        consolidation_result = None
        if result.triggered and result.should_consolidate:
            consolidation_result = consolidate_memories_workflow(
                store=bmstore,
                consolidator=MemoryConsolidator(gateway),
                core_memory=core_memory,
                max_group_size=5,
                trace_id=trace_id,
            )

    yield {
        "step": "batch_classified",
        "data": {
            "triggered": result.triggered,
            "raw_candidates": model_data(result.raw_candidates),
            "final_memories": model_data(result.final_memories),
            "discarded_count": result.discarded_count,
            "self_check_result": model_data(result.self_check_result),
            "batch_classify_count": result.batch_classify_count,
            "should_consolidate": result.should_consolidate,
            "entity_links_created": result.entity_links_created,
            "consolidation": model_data(consolidation_result),
        },
    }

    yield {
        "step": "batch_classify_done",
        "data": {
            "saved_ids": result.saved_ids,
            "classified_pending_ids": result.classified_pending_ids,
            "pending_count": result.pending_count,
            "batch_classify_count": result.batch_classify_count,
            "entity_links_created": result.entity_links_created,
            "consolidation": model_data(consolidation_result),
        },
    }


def consolidate_stream():
    trace_id = str(uuid.uuid4())

    core_memory = core_memory_store().load_required()

    with memory_store() as mem_store:
        active_count = len(mem_store.list_active())
        total_count = mem_store.count()
        gateway = create_gateway()
        gateway.set_trace_context(session_type="maintenance")
        consolidator = MemoryConsolidator(gateway)

        yield {
            "step": "consolidate_started",
            "data": {
                "trace_id": trace_id,
                "active_count": active_count,
                "total_count": total_count,
                "inactive_count": total_count - active_count,
            },
        }

        result = consolidate_memories_workflow(
            store=mem_store,
            consolidator=consolidator,
            core_memory=core_memory,
            max_group_size=5,
            trace_id=trace_id,
        )

    with pending_memory_store() as pending_store:
        pending_store.reset_batch_classify_count()

    yield {
        "step": "consolidate_done",
        "data": {
            "active_before": active_count,
            "groups": len(result.groups),
            "merged": result.merged_count,
            "deactivated": result.deactivated_count,
            "active_after": active_count - result.deactivated_count + result.merged_count,
            "proposals": model_data(result.proposals),
        },
    }
