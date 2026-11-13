"""Create NPCs from the first-stage Event design."""

from app.domain.core_memory.schemas import CoreMemory
from app.domain.creative_brief import draw_creative_brief
from app.domain.director_npc.creator import DirectorNPCCreator, DirectorNPCCreationWarning
from app.domain.director_npc.schemas import DirectorNPCDraft, NPCCreationContext
from app.domain.world_setting.schemas import WorldSetting

from .design import DirectorEventDesign
from .views import project_creation_settings


class DirectorEventActorCreator:
    def __init__(self, gateway):
        self._creator = DirectorNPCCreator(gateway)
        self.last_warnings: list[DirectorNPCCreationWarning] = []

    def create(
        self,
        *,
        design: DirectorEventDesign,
        required_actors: list[dict],
        core_memory: CoreMemory,
        settings: list[WorldSetting],
        existing_npcs: list,
        protagonist_name: str,
        turn_index: int,
        reserved_npcs: list[dict] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, DirectorNPCDraft]:
        """Return newly created drafts by actor_key, without writing campaign state."""
        settings = [
            WorldSetting.model_validate(view)
            for view in project_creation_settings(
                [setting.director_view() for setting in settings],
                design.selected_setting_fact_ids,
            )
        ]
        roles = {role.actor_key: role.role_in_event for role in design.actor_roles}
        if set(roles) != {actor["actor_key"] for actor in required_actors}:
            raise ValueError("actor_roles must cover exactly every required actor_key")
        requests = [
            (
                actor["actor_key"],
                f"{actor['guidance']}\n在本事件中的职责：{roles[actor['actor_key']]}",
            )
            for actor in required_actors
            if actor["source"] == "new"
        ]
        requests.extend((role.actor_key, role.role_in_event) for role in design.additional_npcs)
        recent_creations = [{
            "kind": "event_design",
            **design.model_dump(mode="json"),
            "required_actors": required_actors,
        }]
        reserved = [
            *(reserved_npcs or []),
            *(actor["profile"] for actor in required_actors if actor["source"] == "existing"),
        ]
        drafts: dict[str, DirectorNPCDraft] = {}
        self.last_warnings = []
        for actor_key, guidance in requests:
            draft = self._creator.create(
                core_memory=core_memory,
                existing_npcs=existing_npcs,
                reserved_npcs=list(reserved),
                creation_context=NPCCreationContext(
                    setting_ids=[setting.setting_id for setting in settings],
                ),
                settings=settings,
                creative_brief=draw_creative_brief(),
                protagonist_name=protagonist_name,
                turn_index=turn_index,
                trace_id=trace_id,
                guidance=guidance,
                recent_creations=list(recent_creations),
            )
            self.last_warnings.extend(self._creator.last_warnings)
            drafts[actor_key] = draft
            profile = draft.private_view().model_dump(mode="json")
            reserved.append(profile)
            recent_creations.append({"kind": "npc", "actor_key": actor_key, **profile})
        return drafts
