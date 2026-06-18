import json

import pytest

from app.core.llm import MockLLM
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.action_preview.schemas import ActionPreview, ActionPreviewResult
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import CurrentState, NPCState, PlayerState, ResourcesState, SceneState
from app.domain.scene_summary.schemas import SceneSummary
from app.workflows.build_action_parse_from_preview import build_parse_from_preview
from app.workflows.generate_action_preview import generate_action_preview_workflow


def _make_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="北郡修道院酒馆",
            participants=["卢西安", "米莉"],
            atmosphere="紧张",
        ),
        player=PlayerState(
            name="卢西安",
            condition=[],
            position="酒馆吧台旁",
        ),
        npcs=[
            NPCState(
                name="米莉",
                role="酒馆侍者",
                location="吧台后",
                attitude_to_player="谨慎友善",
            )
        ],
        resources=ResourcesState(inventory=["旧地图"], money=50),
    )


def _make_core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="core_world",
            category="world",
            content="当前世界是经典旧世时代的艾泽拉斯。",
            priority=100,
        )
    ])


def _make_recent_summaries() -> list[SceneSummary]:
    return []


def _build_response() -> str:
    return json.dumps({
        "scene_context": "卢西安在北郡修道院酒馆里，刚从米莉那里得到仓库区的线索。",
        "previews": [
            {
                "action_id": "ap_1",
                "level": "冒险举动",
                "label": "保持警惕，观察酒馆里的异常动静。",
                "description": "你先确认周围是否有人注意到这段谈话。",
                "good_result": "你能发现潜在的盯梢者或排除眼前危险。",
                "risk": "过度警觉可能引起旁人的怀疑。",
                "action_type": "observe",
                "verb": "观察",
                "target": "酒馆",
                "method": "保持警惕",
                "goal": "发现异常动静",
                "likely_needs_roll": False,
                "risk_level": "low",
                "anchors": [
                    {"text": "酒馆", "normalized": "酒馆", "kind": "location", "confidence": 0.9}
                ],
            },
            {
                "action_id": "ap_2",
                "level": "保守举动",
                "label": "继续询问米莉，确认仓库区的具体位置。",
                "description": "你追问线索的细节，争取更明确的方向。",
                "good_result": "米莉可能给出更准确的地点或可识别标记。",
                "risk": "追问太急会让米莉感到不安。",
                "action_type": "speak",
                "verb": "询问",
                "target": "米莉",
                "method": "追问细节",
                "goal": "确认仓库区位置",
                "likely_needs_roll": False,
                "risk_level": "low",
                "anchors": [
                    {"text": "米莉", "normalized": "米莉", "kind": "character", "confidence": 0.95},
                    {"text": "仓库区", "normalized": "仓库区", "kind": "location", "confidence": 0.9},
                ],
            },
            {
                "action_id": "ap_3",
                "level": "信息获取",
                "label": "动身前往仓库区外围查看情况。",
                "description": "你把线索转化为实地侦查。",
                "good_result": "你能接近线索源头并发现新的场景信息。",
                "risk": "仓促前往可能撞上未知危险。",
                "action_type": "move",
                "verb": "前往",
                "target": "仓库区外围",
                "method": None,
                "goal": "查看情况",
                "likely_needs_roll": False,
                "risk_level": "medium",
                "anchors": [
                    {"text": "仓库区外围", "normalized": "仓库区外围", "kind": "location", "confidence": 0.9}
                ],
            },
            {
                "action_id": "ap_4",
                "level": "奇招",
                "label": "先整理米莉的话和旧地图上的路线。",
                "description": "你在行动前把线索和资源对应起来。",
                "good_result": "你能制定更稳妥的前往路线。",
                "risk": "拖延可能让线索随时间变冷。",
                "action_type": "plan",
                "verb": "整理",
                "target": "线索",
                "method": "对照旧地图",
                "goal": "制定路线",
                "likely_needs_roll": False,
                "risk_level": "low",
                "anchors": [
                    {"text": "旧地图", "normalized": "旧地图", "kind": "item", "confidence": 0.95},
                    {"text": "路线", "normalized": "路线", "kind": "concept", "confidence": 0.8},
                ],
            },
        ],
    }, ensure_ascii=False)


class TestActionPreviewAgent:
    def test_generates_valid_preview_result(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        result = ActionPreviewAgent(mock.build_gateway()).generate(
            scene_text="米莉压低声音，提到仓库区最近有人夜里出没。",
            current_state=_make_state(),
            core_memory=_make_core_memory(),
            recent_summaries=_make_recent_summaries(),
        )

        assert isinstance(result, ActionPreviewResult)
        assert len(result.previews) == 4
        assert {preview.level for preview in result.previews} == {"冒险举动", "保守举动", "信息获取", "奇招"}
        assert all(preview.anchors for preview in result.previews)

    def test_workflow_uses_agent(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        result = generate_action_preview_workflow(
            scene_text="米莉压低声音，提到仓库区最近有人夜里出没。",
            current_state=_make_state(),
            core_memory=_make_core_memory(),
            recent_summaries=_make_recent_summaries(),
            gateway=mock.build_gateway(),
        )

        assert result.previews[0].action_id == "ap_1"


class TestBuildActionParseFromPreview:
    def test_builds_parse_result_and_runs_self_check(self):
        preview = ActionPreview.model_validate(json.loads(_build_response())["previews"][1])
        checker = ActionSelfCheck()

        result = build_parse_from_preview(preview, _make_state(), self_check=checker)

        assert isinstance(result, ActionParseResult)
        assert len(result.atomic_actions) == 1
        assert result.atomic_actions[0].action_id == "A1"
        assert result.atomic_actions[0].actor == "卢西安"
        assert result.atomic_actions[0].action_type == "speak"
        assert result.atomic_actions[0].raw_text == preview.label
        assert result.atomic_actions[0].target == "米莉"
        assert result.atomic_actions[0].risk_level == preview.risk_level
        assert {anchor.text for anchor in result.anchors} >= {"米莉", "仓库区", "卢西安"}
        assert checker.last_result is not None
        assert checker.last_result.final_action_count == 1

    def test_invalid_action_type_is_rejected_by_schema(self):
        data = json.loads(_build_response())["previews"][0]
        data["action_type"] = "invalid"

        with pytest.raises(Exception):
            ActionPreview.model_validate(data)
