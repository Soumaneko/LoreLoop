import pytest
from pydantic import ValidationError

from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.turn_resolution import ActionResolutionFrame, TurnResolutionResult
from app.domain.plot_outline.schemas import PlotOutlineContext
from app.graph.generation_helpers import assemble_context


def _make_current_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="北郡修道院",
            time_label="第1日深夜",
            participants=["主角", "米莉"],
            atmosphere="宁静",
        ),
        player=PlayerState(
            name="主角",
            condition=[],
            position="修道院大厅",
            visible_status="正常",
            intent="探索",
        ),
        npcs=[
            NPCState(
                name="米莉",
                role="酒馆老板",
                location="酒馆吧台",
                condition=[],
                attitude_to_player="友好",
                current_intent="招待客人",
            ),
        ],
        resources=ResourcesState(inventory=[InventoryItem(name="旧地图")], money=50),
    )


def _make_turn_resolution() -> TurnResolutionResult:
    return TurnResolutionResult(
        action_resolutions=[
            ActionResolutionFrame(
                action_id="a1",
                sequence=1,
                actor=["主角"],
                action_type="observe",
                outcome_band="success",
                effect_reasoning="调查属于 A 类，玩家意图查看大厅，success 给出清晰发现。",
                resolved_fact="主角发现修道院大厅中的关键异常。",
            ),
        ],
    )


def _make_core_memory() -> CoreMemory:
    return CoreMemory(
        items=[
            CoreMemoryItem(
                id="core_player_identity",
                category="player",
                content="主角是卢西安，人类法师。",
                priority=100,
            ),
        ]
    )


class TestAssembleContext:
    def test_full_assembly(self):
        ctx = assemble_context(
            user_input="环顾四周观察环境。",
            current_state=_make_current_state(),
            turn_resolution=_make_turn_resolution(),
            core_memory=_make_core_memory(),
        )

        assert ctx.user_input == "环顾四周观察环境。"
        assert ctx.current_state.scene.scene_id == "scene_001"
        assert ctx.turn_resolution.total_resolutions == 1
        assert len(ctx.core_memory.items) == 1
        assert ctx.context_facts.facts == []

    def test_empty_core_memory(self):
        ctx = assemble_context(
            user_input="测试",
            current_state=_make_current_state(),
            turn_resolution=_make_turn_resolution(),
            core_memory=CoreMemory(items=[]),
        )
        assert ctx.core_memory.items == []

    def test_serialization_roundtrip(self):
        ctx = assemble_context(
            user_input="测试",
            current_state=_make_current_state(),
            turn_resolution=_make_turn_resolution(),
            core_memory=_make_core_memory(),
        )

        json_str = ctx.model_dump_json()
        restored = PlotOutlineContext.model_validate_json(json_str)

        assert restored.user_input == ctx.user_input
        assert restored.current_state.scene.scene_id == ctx.current_state.scene.scene_id
        assert restored.core_memory.items[0].id == ctx.core_memory.items[0].id
        assert restored.turn_resolution.action_resolutions[0].outcome_band == "success"

    def test_subtype_validation_rejects_wrong_subtype(self):
        with pytest.raises(ValidationError):
            PlotOutlineContext(
                user_input="测试",
                current_state="not_a_state",  # type: ignore[arg-type]
                turn_resolution=_make_turn_resolution(),
                core_memory=_make_core_memory(),
            )
