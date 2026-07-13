from .serialization import model_data
from .session import PENDING_OPENINGS, PENDING_TURNS, pending_turn_variants
from .settings import load_settings
from .stores import (
    core_memory_store,
    current_state_store,
    director_event_store,
    director_npc_store,
    entity_store,
    memory_store,
    npc_profile_store,
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
        pending_unclassified_count = pending_store.count_unclassified()
        pending_unclassified = pending_store.list_recent_unclassified(limit=20)
        pending_unclassified_turn_count = pending_store.unclassified_turn_count()

    with scene_summary_store() as summary_store:
        scene_summaries = summary_store.list_active()
        recent_summaries = summary_store.get_recent(5)

    with entity_store() as ent_store:
        entity_profiles = ent_store.list_all()

    with npc_profile_store() as npc_store:
        npc_profiles = npc_store.list_all()
        npc_profiles_active = npc_store.list_all(active_only=True)
        npc_profile_count = len(npc_profiles)
        npc_active_profile_count = len(npc_profiles_active)

    with director_event_store() as director_store:
        active_director_events = director_store.list_active()
        resolved_director_events = director_store.list_resolved()

    with director_npc_store() as npc_store:
        director_npcs = npc_store.list_all()
        standby_director_npcs = [npc for npc in director_npcs if npc.status == "standby"]
        dispatched_director_npcs = [npc for npc in director_npcs if npc.status == "dispatched"]
        last_director_npc_dispatch_turn = npc_store.get_meta_int("last_dispatch_turn", 0)

    settings = load_settings()
    core_memory = core_memory_store().load()
    group_ids = list(dict.fromkeys(
        pending.group_id for pending in PENDING_TURNS.values()
    ))
    pending_turn_groups = [
        {
            "group_id": group_id,
            "user_input": variants[0].user_input,
            "difficulty": variants[0].difficulty,
            "max_chars": variants[0].max_chars,
            "variants": [
                {
                    "turn_id": variant.turn_id,
                    "version_index": variant.version_index,
                    "turn_index": variant.turn_index,
                    "scene_text": variant.scene_result.scene_text,
                    "closing_prompt": variant.scene_result.closing_prompt,
                }
                for variant in variants
            ],
        }
        for group_id in group_ids
        if (variants := pending_turn_variants(group_id))
    ]
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
        "pending_unclassified_count": pending_unclassified_count,
        "pending_unclassified_turn_count": pending_unclassified_turn_count,
        "pending_memory_items": model_data(pending_unclassified),
        "scene_summaries": model_data(scene_summaries),
        "recent_summaries": model_data(recent_summaries),
        "entity_profiles": model_data(entity_profiles),
        "npc_profiles": model_data(npc_profiles),
        "npc_profile_count": npc_profile_count,
        "npc_active_profile_count": npc_active_profile_count,
        "active_director_events": model_data(active_director_events),
        "resolved_director_events": model_data(resolved_director_events),
        "resolved_director_event_count": len(resolved_director_events),
        "director_npcs": model_data(director_npcs),
        "standby_director_npcs": model_data(standby_director_npcs),
        "dispatched_director_npcs": model_data(dispatched_director_npcs),
        "last_director_npc_dispatch_turn": last_director_npc_dispatch_turn,
        "chat_history": model_data(chat_history),
        "difficulty": settings.get("difficulty", "困难"),
        "max_chars": int(settings.get("max_chars", 1500)),
        "pending_turn_groups": pending_turn_groups,
        "pending_opening_ids": [*PENDING_OPENINGS.keys()],
    }
