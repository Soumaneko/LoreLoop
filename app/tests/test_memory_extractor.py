import json

import pytest

from app.core.llm import LLMOutputValidationError, MockLLM
from app.domain.recall_memory.extractor import MemoryExtractor
from app.domain.recall_memory.schemas import Memory, MemoryExtractResult, MemoryType
from app.domain.current_state import CurrentState, SceneState, PlayerState, ResourcesState

def _cs(scene_id="test", location="", time_label="", participants=None, viewpoint="主角"):
    return CurrentState(
        scene=SceneState(scene_id=scene_id, location=location, time_label=time_label, participants=participants or []),
        player=PlayerState(name=viewpoint),
        resources=ResourcesState(),
    )



def _build_llm_response(memories: list[dict]) -> str:
    return json.dumps({"memories": memories}, ensure_ascii=False)


def _make_extractor(responses: list[str]) -> MemoryExtractor:
    mock = MockLLM()
    mock.set_responses(responses)
    return MemoryExtractor(mock.build_gateway())


class TestMemoryExtractorNormal:
    def test_extract_agreement_type(self):
        extractor = _make_extractor([
            _build_llm_response([
                {
                    "type": "agreement",
                    "subject": "沉默暗号",
                    "content": "主角和伊莱娜约定：左手轻敲两下桌面表示保持沉默。",
                    "entities": ["主角", "伊莱娜"],
                    "location": "灰鹭旅店二楼",
                    "time_label": "第2日夜晚",
                    "visibility": ["主角", "伊莱娜"],
                    "tags": ["暗号", "沉默", "约定"],
                    "certainty": 1.0,
                    "salience": 0.8,
                    "source_scene_id": "scene_002",
                    "source_text_span": ["左手轻敲两下桌面，就表示让她保持沉默"],
                }
            ])
        ])

        result = extractor.extract(
            scene_text="主角告诉伊莱娜，如果他用左手轻敲两下桌面，就表示让她保持沉默。伊莱娜点头答应。",
            current_state=_cs(scene_id="scene_002", location="灰鹭旅店二楼", time_label="第2日夜晚", viewpoint="主角", participants=["主角", "伊莱娜"]),
        )

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) == 1
        m = result.memories[0]
        assert m.type == "agreement"
        assert m.subject == "沉默暗号"
        assert "伊莱娜" in m.entities
        assert m.location == "灰鹭旅店二楼"
        assert m.certainty == 1.0
        assert m.salience == 0.8

    def test_extract_trait_type(self):
        extractor = _make_extractor([
            _build_llm_response([
                {
                    "type": "trait",
                    "subject": "伊莱娜谨慎",
                    "content": "伊莱娜性格谨慎，每次行动前会观察四周。",
                    "entities": ["伊莱娜"],
                    "location": None,
                    "time_label": None,
                    "visibility": ["主角"],
                    "tags": ["性格", "谨慎"],
                    "certainty": 0.9,
                    "salience": 0.7,
                    "source_scene_id": "scene_003",
                    "source_text_span": ["总是先环顾四周，确认没有可疑人物才开口说话"],
                }
            ])
        ])

        result = extractor.extract(
            scene_text="伊莱娜在进入房间前总是先环顾四周，确认没有可疑人物才开口说话。",
            current_state=_cs(scene_id="scene_003", location="灰鹭旅店一楼", time_label="第3日清晨", viewpoint="主角", participants=["主角", "伊莱娜"]),
        )

        assert len(result.memories) == 1
        assert result.memories[0].type == "trait"
        assert result.memories[0].subject == "伊莱娜谨慎"

    def test_extract_item_type(self):
        extractor = _make_extractor([
            _build_llm_response([
                {
                    "type": "item",
                    "subject": "银色怀表",
                    "content": "主角从老者处获得一枚银色怀表，背面刻有奇怪的符文。",
                    "entities": ["主角", "老者"],
                    "location": "钟表店",
                    "time_label": "第1日午后",
                    "visibility": ["主角"],
                    "tags": ["道具", "怀表", "符文"],
                    "certainty": 1.0,
                    "salience": 0.9,
                    "source_scene_id": "scene_001",
                    "source_text_span": ["一枚银色怀表递给主角，背面刻着奇怪的符文"],
                }
            ])
        ])

        result = extractor.extract(
            scene_text="老者从柜台下取出一枚银色怀表递给主角，背面刻着奇怪的符文。",
            current_state=_cs(scene_id="scene_001", location="钟表店", time_label="第1日午后", viewpoint="主角", participants=["主角", "老者"]),
        )

        assert len(result.memories) == 1
        assert result.memories[0].type == "item"
        assert result.memories[0].subject == "银色怀表"

    def test_extract_event_type(self):
        extractor = _make_extractor([
            _build_llm_response([
                {
                    "type": "event",
                    "subject": "钟楼爆炸",
                    "content": "城市中心钟楼发生爆炸，守卫队封锁了周边区域。",
                    "entities": ["守卫队"],
                    "location": "城市中心",
                    "time_label": "第2日黄昏",
                    "visibility": ["主角", "市民"],
                    "tags": ["爆炸", "封锁"],
                    "certainty": 1.0,
                    "salience": 0.9,
                    "source_scene_id": "scene_005",
                    "source_text_span": ["钟楼突然爆炸"],
                }
            ])
        ])

        result = extractor.extract(
            scene_text="一声巨响，城市中心的钟楼突然爆炸。守卫队迅速封锁了周边区域。",
            current_state=_cs(scene_id="scene_005", location="城市中心", time_label="第2日黄昏", viewpoint="主角", participants=["主角", "市民"]),
        )

        assert len(result.memories) == 1
        assert result.memories[0].type == "event"

    def test_empty_scene_returns_empty_memories(self):
        extractor = _make_extractor([
            _build_llm_response([])
        ])

        result = extractor.extract(
            scene_text="风吹过街道，树叶沙沙作响。远处传来隐约的鸟鸣。",
            current_state=_cs(scene_id="scene_010", location="街道", time_label="清晨", viewpoint="主角", participants=[]),
        )

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) == 0

    def test_multiple_memories_from_one_scene(self):
        extractor = _make_extractor([
            _build_llm_response([
                {
                    "type": "item",
                    "subject": "宝剑",
                    "content": "主角从铁匠铺取回了定制的宝剑。",
                    "entities": ["主角"],
                    "location": "铁匠铺",
                    "time_label": "第1日正午",
                    "visibility": ["主角"],
                    "tags": ["武器"],
                    "certainty": 1.0,
                    "salience": 0.6,
                    "source_scene_id": "scene_001",
                    "source_text_span": ["定制的宝剑终于完成了"],
                },
                {
                    "type": "fact",
                    "subject": "铁匠认识主角父亲",
                    "content": "铁匠老王说他认识主角的父亲，两人曾是战友。",
                    "entities": ["铁匠老王", "主角", "主角父亲"],
                    "location": "铁匠铺",
                    "time_label": "第1日正午",
                    "visibility": ["主角", "铁匠老王"],
                    "tags": ["背景", "父亲"],
                    "certainty": 0.8,
                    "salience": 0.7,
                    "source_scene_id": "scene_001",
                    "source_text_span": ["我和你父亲以前是战友"],
                }
            ])
        ])

        result = extractor.extract(
            scene_text="主角到铁匠铺取剑。铁匠老王说：\"定制的宝剑终于完成了。其实我和你父亲以前是战友。\"",
            current_state=_cs(scene_id="scene_001", location="铁匠铺", time_label="第1日正午", viewpoint="主角", participants=["主角", "铁匠老王"]),
        )

        assert len(result.memories) == 2
        types = {m.type for m in result.memories}
        assert "item" in types
        assert "fact" in types


class TestMemoryExtractorErrors:
    def test_schema_validation_failure_propagates(self):
        extractor = _make_extractor([
            '{"memories": [{"type": "invalid_type", "subject": "test", "content": "test"}]}',
            "not valid json",
        ])

        with pytest.raises(LLMOutputValidationError):
            extractor.extract(
                scene_text="some text",
                current_state=_cs(scene_id="s1"),
                max_retries=1,
            )

    def test_llm_returns_non_json_propagates_error(self):
        extractor = _make_extractor(["not json at all"])

        with pytest.raises(LLMOutputValidationError):
            extractor.extract(
                scene_text="some text",
                current_state=_cs(scene_id="s1"),
                max_retries=0,
            )


class TestMemoryExtractorState:
    def test_check_results_default_to_none(self):
        extractor = _make_extractor([
            _build_llm_response([])
        ])

        assert extractor.last_check_result is None
        assert extractor.last_llm_check_result is None

        extractor.extract(
            scene_text="风吹过街道。",
            current_state=_cs(scene_id="scene_empty"),
        )

        assert extractor.last_check_result is None
        assert extractor.last_llm_check_result is None
