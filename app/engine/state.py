from .serialization import model_data
from .session import PENDING_OPENINGS, PENDING_TURNS
from .settings import load_settings
from .stores import (
    core_memory_store,
    current_state_store,
    entity_store,
    memory_store,
    pending_memory_store,
    scene_summary_store,
    short_term_memory_store,
)


def load_debug_state() -> dict:
    with current_state_store() as state_store:
        current_state = state_store.load_or_default()

    with short_term_memory_store() as short_store:
        short_term = short_store.get_recent()
        chat_history = short_store.list_all()

    with memory_store() as mem_store:
        memory_count = mem_store.count()
        memories = mem_store.list_all()
        active_memory_count = len(mem_store.list_active())
        inactive_memory_count = memory_count - active_memory_count

    with pending_memory_store() as pending_store:
        pending_count = pending_store.count()
        pending_unclassified = pending_store.list_recent_unclassified(limit=20)
        pending_unclassified_turn_count = pending_store.unclassified_turn_count()

    with scene_summary_store() as summary_store:
        scene_summaries = summary_store.list_active()
        recent_summaries = summary_store.get_recent(5)

    with entity_store() as ent_store:
        entity_profiles = ent_store.list_all()

    settings = load_settings()
    core_memory = core_memory_store().load()
    return {
        "needs_init": core_memory is None,
        "current_state": model_data(current_state),
        "core_memory": model_data(core_memory) if core_memory is not None else None,
        "short_term_memory": model_data(short_term),
        "memory_count": memory_count,
        "active_memory_count": active_memory_count,
        "inactive_memory_count": inactive_memory_count,
        "memories": model_data(memories),
        "pending_memory_count": pending_count,
        "pending_unclassified_turn_count": pending_unclassified_turn_count,
        "pending_memory_items": model_data(pending_unclassified),
        "scene_summaries": model_data(scene_summaries),
        "recent_summaries": model_data(recent_summaries),
        "entity_profiles": model_data(entity_profiles),
        "chat_history": model_data(chat_history),
        "difficulty": settings.get("difficulty", "困难"),
        "max_chars": int(settings.get("max_chars", 1500)),
        "pending_turn_ids": [*PENDING_TURNS.keys(), *PENDING_OPENINGS.keys()],
    }
