import json
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.schemas import WorldSetting
from app.domain.views import (
    project_current_state,
    project_scene_summaries,
)

from .schemas import (
    DirectorEvent,
    DirectorEventRole,
    DirectorEventSelectionOutput,
    DirectorEventSelectionResult,
)


def _event_list_view(event: DirectorEvent) -> dict:
    view = {
        "role": event.role,
        "stage": event.stage,
        "title": event.title,
        "subject": event.subject,
        "intent": event.intent,
        "method": event.method,
        "actors": [actor.name for actor in event.actors],
    }
    if event.stage == "resolved":
        view["resolved_reason"] = event.resolved_reason
        view["final_world_impact"] = (
            event.final_world_impact.model_dump(mode="json")
            if event.final_world_impact is not None
            else None
        )
    return view


def _used_fact_ids_by_setting(
    events: list[DirectorEvent],
) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for event in events:
        for setting_id in event.source_setting_ids:
            used.setdefault(setting_id, set()).update(
                event.source_setting_fact_ids
            )
    return used


def event_eligible_setting_ids(
    settings: list[WorldSetting],
    events: list[DirectorEvent],
) -> set[str]:
    used = _used_fact_ids_by_setting(events)
    eligible: set[str] = set()
    for setting in settings:
        fact_count = len(setting.public_facts) + len(setting.hidden_facts)
        current_fact_ids = {
            f"fact_{index}"
            for index in range(1, fact_count + 1)
        }
        if current_fact_ids - used.get(setting.setting_id, set()):
            eligible.add(setting.setting_id)
    return eligible


def _setting_fact_view(
    setting: WorldSetting,
    *,
    used_fact_ids: set[str],
) -> dict:
    view = setting.director_view()
    next_id = 1
    for field_name in ("public_facts", "hidden_facts"):
        facts = []
        for content in view[field_name]:
            fact_id = f"fact_{next_id}"
            next_id += 1
            fact = {"content": content}
            if fact_id not in used_fact_ids:
                fact["fact_id"] = fact_id
            facts.append(fact)
        view[field_name] = facts
    return view


class DirectorEventSelector:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def select_or_create(
        self,
        *,
        role: DirectorEventRole,
        events: list[DirectorEvent],
        current_state: CurrentState,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary] | None = None,
        settings: list[WorldSetting] | None = None,
        required_actors: list[dict] | None = None,
        trace_id: str | None = None,
        guidance: str | None = None,
    ) -> DirectorEventSelectionResult:
        required_actors = required_actors or []
        settings = settings or []
        if len(settings) != 1:
            raise ValueError("event selection requires exactly one Setting")
        used_fact_ids = _used_fact_ids_by_setting(events)
        setting_views = [
            _setting_fact_view(
                setting,
                used_fact_ids=used_fact_ids.get(setting.setting_id, set()),
            )
            for setting in settings
        ]
        available_fact_ids = {
            fact["fact_id"]
            for setting in setting_views
            for field_name in ("public_facts", "hidden_facts")
            for fact in setting[field_name]
            if "fact_id" in fact
        }
        if not available_fact_ids:
            raise ValueError("selected Setting has no unused facts")
        input_data = {
            "events_json": json.dumps(
                [_event_list_view(event) for event in events],
                ensure_ascii=False,
                indent=2,
            ),
            "current_state_json": project_current_state(current_state),
            "core_memory_json": core_memory.model_dump_json(indent=2),
            "recent_summaries_json": project_scene_summaries(recent_summaries or []),
            "settings_json": json.dumps(
                setting_views,
                ensure_ascii=False,
                indent=2,
            ),
            "required_actors_json": json.dumps(
                required_actors,
                ensure_ascii=False,
                indent=2,
            ),
            "director_guidance": guidance or "",
            "output_schema_json": schema_json(DirectorEventSelectionOutput),
        }
        expected_actor_keys = {
            str(actor["actor_key"])
            for actor in required_actors
        }

        def validate_selection(selection: DirectorEventSelectionOutput) -> None:
            actual_actor_keys = {
                role.actor_key
                for role in selection.actor_roles
            }
            if actual_actor_keys != expected_actor_keys:
                raise ValueError(
                    "actor_roles must cover exactly every required actor_key"
                )
            selected_fact_ids = set(selection.selected_setting_fact_ids)
            if not selected_fact_ids:
                raise ValueError(
                    "event selection must choose at least one Setting fact_id"
                )
            invalid_fact_ids = selected_fact_ids - available_fact_ids
            if invalid_fact_ids:
                raise ValueError(
                    "selected_setting_fact_ids are unavailable: "
                    + ", ".join(sorted(invalid_fact_ids))
                )

        return cast(
            DirectorEventSelectionResult,
            self.llm.run(
                task=f"director_event_select_{role}",
                input_data=input_data,
                output_schema=DirectorEventSelectionOutput,
                temperature=0.3,
                trace_id=trace_id,
                output_validator=validate_selection,
            ),
        )
