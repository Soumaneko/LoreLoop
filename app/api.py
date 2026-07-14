"""Public application API used by the debug server and local tools."""

from app.graph.confirmation_graph import confirm_stream
from app.graph.generation_graph import generate_turn_stream, retry_turn_stream
from app.graph.initialization_graph import apply_core_memory_init
from app.graph.initialization_helpers import preview_core_memory
from app.graph.maintenance_graph import batch_classify_stream, memory_maintain_stream
from app.runtime.debug_state import load_debug_state
from app.runtime.settings import save_difficulty, save_max_chars
from app.runtime.saves import create_save, list_saves, switch_save
from app.runtime.session import get_campaign_runtime
from app.runtime.jobs import JobStatus


def get_maintenance_job(job_id: str) -> dict | None:
    job = get_campaign_runtime().jobs.get(job_id)
    return job.as_dict() if job is not None else None


def list_maintenance_jobs(
    *,
    status: JobStatus | None = None,
    job_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    return [
        job.as_dict()
        for job in get_campaign_runtime().jobs.list(
            status=status,
            job_type=job_type,
            limit=limit,
        )
    ]


__all__ = [
    "apply_core_memory_init",
    "batch_classify_stream",
    "confirm_stream",
    "create_save",
    "generate_turn_stream",
    "get_maintenance_job",
    "load_debug_state",
    "list_maintenance_jobs",
    "list_saves",
    "memory_maintain_stream",
    "preview_core_memory",
    "retry_turn_stream",
    "save_difficulty",
    "save_max_chars",
    "switch_save",
]
