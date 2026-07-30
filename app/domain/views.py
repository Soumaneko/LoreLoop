import json
from typing import Any

from app.domain.action.schemas import ActionParseResult
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView, NPCProfilePublicView
from app.domain.scene_summary.schemas import SceneSummary


def strip_empty(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: strip_empty(v)
            for k, v in obj.items()
            if v is not None and v != []
        }
    if isinstance(obj, list):
        return [strip_empty(item) for item in obj]
    return obj


def project_current_state(state: CurrentState) -> str:
    data = state.model_dump(mode="json")
    return json.dumps(
        strip_empty(data),
        ensure_ascii=False,
        indent=2,
    )


def project_entity_profiles(profiles: list[EntityProfileUpdateInput]) -> str:
    return json.dumps(
        [strip_empty(p.model_dump(mode="json")) for p in profiles],
        ensure_ascii=False,
        indent=2,
    )


def project_npc_profiles(
    profiles: list[NPCProfileDirectorView | NPCProfilePublicView],
) -> str:
    return json.dumps(
        [strip_empty(p.model_dump(mode="json")) for p in profiles],
        ensure_ascii=False,
        indent=2,
    )


def project_scene_summaries(summaries: list[SceneSummary]) -> str:
    exclude = {"id", "scene_ids", "source_ids", "created_at", "active", "condensed_into"}
    return json.dumps(
        [s.model_dump(mode="json", exclude=exclude) for s in summaries],
        ensure_ascii=False,
        indent=2,
    )


def project_scene_facts(facts: Any) -> str:
    return json.dumps(
        strip_empty(facts.model_dump(mode="json")),
        ensure_ascii=False,
        indent=2,
    )


def project_action_parse(result: ActionParseResult) -> str:
    actions = []
    for a in result.atomic_actions:
        d = a.model_dump(mode="json", exclude={"likely_needs_roll"})
        if d.get("condition") is None:
            d.pop("condition", None)
        actions.append(d)
    data = {"atomic_actions": actions}
    return json.dumps(data, ensure_ascii=False, indent=2)
