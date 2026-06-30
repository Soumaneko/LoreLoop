import pytest
from pydantic import ValidationError

from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.turn_resolution import ActionResolutionFrame, TurnResolutionResult
from app.workflows.context_assembler import PlotOutlineContext, assemble_context


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


def _make_action_parse_result() -> ActionParseResult:
    return ActionParseResult(
        turn_intent="探索修道院",
        atomic_actions=[
            AtomicAction(
                action_id="a1",
                sequence=1,
                actor="主角",
                action_type="observe",
                raw_text="环顾四周",
                verb="观察",
                target="四周",
                likely_needs_roll=True,
                roll_hint="perception",
                confidence=0.9,
            ),
        ],
        anchors=[],
        ambiguities=[],
    )


def _make_turn_resolution() -> TurnResolutionResult:
    return TurnResolutionResult(
        available_types=["advance", "relationship_shift"],
        selected_type="advance",
        action_resolutions=[
            ActionResolutionFrame(
                action_id="a1",
                sequence=1,
                actor="主角",
                action_type="observe",
                outcome_band="success",
                effect_reasoning="调查属于 A 类，玩家意图查看大厅，success 给出清晰发现。",
                resolved_effect="主角清楚发现修道院大厅中的关键异常。",
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


def _make_retrieved_memories() -> CandidateRetrievalResult:
    return CandidateRetrievalResult(
        candidates=[
            MemoryCandidate(
                memory_id="mem_001",
                memory_kind="open_thread", evidence_mode="observed",
                subject="修道院历史",
                content="北郡修道院曾是大战时期的据点。",
                entities=["北郡修道院"],
                tags=["历史"],
                salience=0.8,
                confidence=0.9,
                score=0.15,
                score_breakdown={"rrf_score": 0.15},
            ),
        ],
        total_scanned=50,
        total_returned=1,
    )


class TestAssembleContext:
    def test_full_assembly(self):
        ctx = assemble_context(
            user_input="环顾四周观察环境。",
            current_state=_make_current_state(),
            action_parse_result=_make_action_parse_result(),
            turn_resolution=_make_turn_resolution(),
            core_memory=_make_core_memory(),
            retrieved_memories=_make_retrieved_memories(),
        )

        assert ctx.user_input == "环顾四周观察环境。"
        assert ctx.current_state.scene.scene_id == "scene_001"
        assert ctx.action_parse_result.turn_intent == "探索修道院"
        assert ctx.turn_resolution.total_resolutions == 1
        assert ctx.turn_resolution.selected_type == "advance"
        assert len(ctx.core_memory.items) == 1
        assert ctx.retrieved_memories.total_returned == 1
        assert ctx.scene_summaries == []

    def test_empty_core_memory(self):
        ctx = assemble_context(
            user_input="测试",
            current_state=_make_current_state(),
            action_parse_result=_make_action_parse_result(),
            turn_resolution=_make_turn_resolution(),
            core_memory=CoreMemory(items=[]),
            retrieved_memories=_make_retrieved_memories(),
        )
        assert ctx.core_memory.items == []

    def test_scene_summaries_are_optional_context(self):
        summary = SceneSummary(
            level=2,
            content="卢西安在旧旅店的调查逐渐指向钟楼。",
            scene_ids=["scene_001", "scene_002"],
            source_ids=["sum_1", "sum_2"],
            turn_start=1,
            turn_end=2,
        )
        ctx = assemble_context(
            user_input="测试",
            current_state=_make_current_state(),
            action_parse_result=_make_action_parse_result(),
            turn_resolution=_make_turn_resolution(),
            core_memory=_make_core_memory(),
            retrieved_memories=_make_retrieved_memories(),
            scene_summaries=[summary],
        )

        assert ctx.scene_summaries == [summary]

    def test_empty_retrieved_memories(self):
        ctx = assemble_context(
            user_input="测试",
            current_state=_make_current_state(),
            action_parse_result=_make_action_parse_result(),
            turn_resolution=_make_turn_resolution(),
            core_memory=_make_core_memory(),
            retrieved_memories=CandidateRetrievalResult(
                candidates=[], total_scanned=0, total_returned=0
            ),
        )
        assert ctx.retrieved_memories.candidates == []
        assert ctx.retrieved_memories.total_scanned == 0

    def test_serialization_roundtrip(self):
        ctx = assemble_context(
            user_input="测试",
            current_state=_make_current_state(),
            action_parse_result=_make_action_parse_result(),
            turn_resolution=_make_turn_resolution(),
            core_memory=_make_core_memory(),
            retrieved_memories=_make_retrieved_memories(),
        )

        json_str = ctx.model_dump_json()
        restored = PlotOutlineContext.model_validate_json(json_str)

        assert restored.user_input == ctx.user_input
        assert restored.current_state.scene.scene_id == ctx.current_state.scene.scene_id
        assert restored.core_memory.items[0].id == ctx.core_memory.items[0].id
        assert restored.turn_resolution.action_resolutions[0].outcome_band == "success"
        assert restored.turn_resolution.selected_type == "advance"

    def test_subtype_validation_rejects_wrong_subtype(self):
        with pytest.raises(ValidationError):
            PlotOutlineContext(
                user_input="测试",
                current_state="not_a_state",  # type: ignore[arg-type]
                action_parse_result=_make_action_parse_result(),
                turn_resolution=_make_turn_resolution(),
                core_memory=_make_core_memory(),
                retrieved_memories=_make_retrieved_memories(),
            )
