from .current_state_access import load_effective_current_state
from .serialization import model_data
from .session import get_campaign_runtime
from .settings import load_settings


def load_debug_state() -> dict:
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        return _load_debug_state(runtime)


def _load_debug_state(runtime) -> dict:
    stores = runtime.stores
    current_state = load_effective_current_state(runtime)
    initialization = runtime.initialization.load()

    with stores.short_term() as short_store:
        short_term = short_store.get_recent()
        chat_history = short_store.list_all()

    with stores.continuity() as continuity_store:
        continuity_items = continuity_store.list_all()
        active_continuity_items = continuity_store.list_active()

    with stores.scene_summary() as summary_store:
        scene_summaries = summary_store.list_active()
        recent_summaries = summary_store.get_recent(5)

    with stores.entity() as ent_store:
        entity_profiles = ent_store.list_all()

    with stores.npc_profile() as npc_store:
        npc_profiles = npc_store.list_all()
        npc_profiles_active = npc_store.list_all(active_only=True)
        npc_profile_count = len(npc_profiles)
        npc_active_profile_count = len(npc_profiles_active)

    with stores.director_event() as director_store:
        active_director_events = director_store.list_active()
        resolved_director_events = director_store.list_resolved()

    with stores.director_npc() as npc_store:
        director_npcs = npc_store.list_all()
        prepared_director_npcs = [
            npc for npc in director_npcs if npc.status == "prepared"
        ]
        introduced_director_npcs = [
            npc for npc in director_npcs if npc.status == "introduced"
        ]

    settings = load_settings()
    core_memory = stores.core_memory().load()
    group_ids = list(dict.fromkeys(
        pending.group_id for pending in runtime.pending_turns.values()
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
        if (variants := runtime.pending_turns.variants(group_id))
    ]
    pending_openings = [
        {
            "turn_id": opening.turn_id,
            "config": model_data(opening.config),
            "current_state": model_data(opening.current_state),
            "scene_result": model_data(opening.scene_result),
        }
        for opening in runtime.pending_openings.values()
    ]
    return {
        "save_name": runtime.campaign_id,
        "save_path": str(runtime.paths.data_dir.resolve()),
        "turn_history": runtime.history.status(),
        "needs_init": (
            (
                initialization is None
                or initialization.phase != "completed"
            )
            and not runtime.pending_openings.values()
        ),
        "initialization": model_data(initialization),
        "current_state": model_data(current_state),
        "core_memory": model_data(core_memory) if core_memory is not None else None,
        "short_term_memory": model_data(short_term),
        "continuity_items": model_data(continuity_items),
        "continuity_count": len(continuity_items),
        "active_continuity_count": len(active_continuity_items),
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
        "prepared_director_npcs": model_data(prepared_director_npcs),
        "introduced_director_npcs": model_data(introduced_director_npcs),
        "chat_history": model_data(chat_history),
        "difficulty": settings.get("difficulty", "困难"),
        "max_chars": int(settings.get("max_chars", 1500)),
        "pending_turn_groups": pending_turn_groups,
        "pending_opening_ids": [
            opening.turn_id for opening in runtime.pending_openings.values()
        ],
        "pending_openings": pending_openings,
        "maintenance_jobs": [
            job.as_dict() for job in runtime.jobs.list(limit=100)
        ],
        "maintenance_job_status_counts": runtime.jobs.status_counts(),
    }
