from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director.schemas import DirectorStageResult
from app.domain.director_event.schemas import DirectorEvent
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.plot_outline.schemas import PlannedEvent, PlotOutlineResult
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.domain.action_judgment.schemas import ActionJudgment, OutcomeBand


class NarrativeComposerContext(BaseModel):
    user_input: str
    current_state: CurrentState
    action_judgments: list[ActionJudgment] = Field(default_factory=list)
    core_memory: CoreMemory
    context_facts: ContextFactSet = Field(
        default_factory=lambda: ContextFactSet(
            turn_focus="No Context Facts",
            facts=[],
        )
    )
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)
    director_stage: DirectorStageResult | None = None
    engaged_events: list[DirectorEvent] = Field(default_factory=list)


class NarrativeComposerResult(BaseModel):
    director_reason: str | None = None
    action_resolutions: list[ActionResolution] = Field(
        default_factory=list,
        description="逐个锁定每个 action 的成败事实，必须 100% 覆盖所有 action_id",
    )
    scene_stopping_point: str = Field(
        min_length=1,
        description="在本回合全部确定结果落实后停留的唯一可交互状态。必须是具体事实快照",
    )
    planned_events: list[PlannedEvent] = Field(
        default_factory=list,
        min_length=1,
        max_length=4,
        description="用 1-4 个按发生顺序排列的确定事件，把语义骨架具体演出来",
    )
    scene_reasoning: str = Field(
        min_length=1,
        description="2-4 句写作前审计。确认正文将如何落实刚刚制定的规划",
    )
    scene_text: str = Field(min_length=1, description="正文")
    closing_prompt: str = Field(min_length=1, description="结尾提示")
    next_action_directions: list[NextActionDirection] = Field(
        default_factory=list,
        max_length=5,
    )

    def to_outline(self) -> PlotOutlineResult:
        return PlotOutlineResult.model_validate(
            self.model_dump(
                include={
                    "director_reason",
                    "action_resolutions",
                    "scene_stopping_point",
                    "planned_events",
                }
            )
        )

    def to_scene_result(self) -> SceneWriterResult:
        return SceneWriterResult.model_validate(
            self.model_dump(
                include={
                    "scene_reasoning",
                    "scene_text",
                    "closing_prompt",
                    "next_action_directions",
                }
            )
        )


class ActionResolution(BaseModel):
    action_id: str = Field(description="对应玩家输入的 action_id (A1, A2...)")
    outcome_band: OutcomeBand = Field(
        description="严格继承程序给定的成败档位"
    )
    resolved_fact: str = Field(
        min_length=1,
        max_length=180,
        description="一句话锁定直接客观事实结论",
    )
