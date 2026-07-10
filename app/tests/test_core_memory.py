import json

import pytest
from pydantic import ValidationError

from app.core.llm import MockLLM
from app.domain.core_memory import (
    CoreMemory,
    CoreMemoryItem,
    CoreMemoryStore,
    InitConfig,
    StoryDirectionAgent,
    StoryDirectionResult,
    build_core_memory,
    build_initial_current_state,
)
from app.domain.current_state import CurrentStateStore
from app.graph.initialization_helpers import preview_core_memory
from app.runtime.session import PENDING_OPENINGS


class TestCoreMemoryItem:
    def test_default_priority_is_100(self):
        item = CoreMemoryItem(id="test", category="world", content="测试内容")
        assert item.priority == 100

    def test_custom_priority(self):
        item = CoreMemoryItem(id="test", category="world", content="测试", priority=50)
        assert item.priority == 50

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            CoreMemoryItem(id="", category="world", content="测试")

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            CoreMemoryItem(id="test", category="world", content="")

    def test_negative_priority_rejected(self):
        with pytest.raises(ValidationError):
            CoreMemoryItem(id="test", category="world", content="测试", priority=-1)


class TestInitConfig:
    def test_fanfic_requires_work_birth_and_era(self):
        with pytest.raises(ValidationError):
            InitConfig(fanfic=True, protagonist_name="卢西安")

    def test_original_requires_world_fields(self):
        with pytest.raises(ValidationError):
            InitConfig(fanfic=False, protagonist_name="卢西安")

    def test_protagonist_name_required(self):
        with pytest.raises(ValidationError):
            InitConfig(
                fanfic=False,
                world_name="艾兰",
                world_style="西幻",
                protagonist_name="",
            )

    def test_ability_limit_is_three(self):
        with pytest.raises(ValidationError):
            InitConfig(
                fanfic=False,
                world_name="艾兰",
                world_style="西幻",
                protagonist_name="卢西安",
                protagonist_abilities=["a", "b", "c", "d"],
            )


class TestBuildCoreMemory:
    def test_builds_fanfic_world_identity(self):
        cm = build_core_memory(InitConfig(
            fanfic=True,
            work_name="魔兽世界",
            birth_location="北郡",
            story_era="经典旧世",
            protagonist_name="卢西安",
        ))

        world = next(item for item in cm.items if item.id == "core_world_identity")
        assert world.category == "world"
        assert "原作《魔兽世界》" in world.content
        assert "北郡" in world.content
        assert "经典旧世" in world.content

    def test_builds_original_world_identity(self):
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            ability_system="魔法",
            protagonist_name="卢西安",
        ))

        world = next(item for item in cm.items if item.id == "core_world_identity")
        assert world.content == "当前世界名为「艾兰」，是一个西幻风格的原创世界。能力体系为魔法。"

    def test_original_world_identity_omits_ability_system_when_empty(self):
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="好莱坞",
            world_style="1990年代娱乐业",
            protagonist_name="莱尔",
        ))

        world = next(item for item in cm.items if item.id == "core_world_identity")
        assert world.content == "当前世界名为「好莱坞」，是一个1990年代娱乐业风格的原创世界。"
        assert "能力体系" not in world.content

    def test_original_world_identity_uses_custom_ability_system(self):
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="星港",
            world_style="太空歌剧",
            ability_system="自定义",
            custom_ability_system="星舰核心与基因调律",
            protagonist_name="莱拉",
        ))

        world = next(item for item in cm.items if item.id == "core_world_identity")
        assert "能力体系为星舰核心与基因调律" in world.content

    def test_player_identity_optional_age_and_background(self):
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            protagonist_gender="男",
            protagonist_name="卢西安",
            protagonist_age=22,
            protagonist_background="出身北郡修道院。",
        ))

        player = next(item for item in cm.items if item.id == "core_player_identity")
        assert player.content == "主角是卢西安，男性，22岁。\n背景：出身北郡修道院。"

    def test_abilities_are_conditional_list(self):
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            protagonist_name="卢西安",
            protagonist_abilities=["旁观者之瞳：顺应历史时获得运势加成", "闪现"],
        ))

        abilities = next(item for item in cm.items if item.id == "core_player_abilities")
        assert abilities.content == "主角的特殊能力：\n- 旁观者之瞳：顺应历史时获得运势加成\n- 闪现"

    def test_no_abilities_item_when_empty(self):
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            protagonist_name="卢西安",
        ))

        assert all(item.id != "core_player_abilities" for item in cm.items)

    def test_tone_and_extra_items_are_appended(self):
        extra = CoreMemoryItem(id="extra_rule", category="world", content="额外规则", priority=80)
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            protagonist_name="卢西安",
            narrative_tone="冷峻写实",
            extra_core_memory_items=[extra],
        ))

        assert next(item for item in cm.items if item.id == "core_tone").content == "冷峻写实"
        assert cm.items[-1] == extra

    def test_story_expectation_is_not_added_without_llm_summary(self):
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="好莱坞",
            world_style="1990年代娱乐业",
            protagonist_name="莱尔",
            story_expectation="经营经纪公司，发掘演员并建立事业与浪漫关系。",
        ))

        assert all(item.id != "core_story_direction" for item in cm.items)

    def test_empty_core_memory_still_valid(self):
        cm = CoreMemory(items=[])
        assert cm.items == []


class TestStoryDirectionAgent:
    def test_generates_single_positive_core_memory_item(self):
        config = InitConfig(
            fanfic=False,
            world_name="好莱坞",
            world_style="1990年代娱乐业",
            protagonist_name="莱尔",
            protagonist_background="穿越者，准备经营一家小型经纪公司。",
            story_expectation="经营经纪公司，发掘演员，体验事业成长和浪漫关系。",
        )
        base_core_memory = build_core_memory(config)
        mock = MockLLM()
        mock.set_responses([
            json.dumps({
                "content": (
                    "剧情围绕1990年代好莱坞的小型经纪公司经营展开，持续关注演员发掘、"
                    "项目争取、行业人脉、事业扩张，以及事业合作中逐步发展的浪漫关系。"
                ),
                "currency_unit": "美元",
            }, ensure_ascii=False)
        ])

        result = StoryDirectionAgent(mock.build_gateway()).generate(
            config=config,
            base_core_memory=base_core_memory,
        )
        item = result.to_core_memory_item()

        assert isinstance(result, StoryDirectionResult)
        assert item.id == "core_story_direction"
        assert item.category == "story"
        assert item.priority == 100
        assert "经纪公司经营" in item.content
        assert result.currency_unit == "美元"
        currency = result.to_core_memory_items()[1]
        assert currency.id == "core_currency_system"
        assert currency.content == "本游戏默认货币单位为「美元」。"

    def test_rejects_empty_story_expectation(self):
        config = InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            protagonist_name="卢西安",
        )

        with pytest.raises(ValueError, match="story_expectation"):
            StoryDirectionAgent(MockLLM().build_gateway()).generate(
                config=config,
                base_core_memory=build_core_memory(config),
            )

    def test_core_memory_item_shape_is_fixed(self):
        result = StoryDirectionResult(content="剧情聚焦冒险、成长与关系发展。")

        assert result.to_core_memory_item() == CoreMemoryItem(
            id="core_story_direction",
            category="story",
            content="剧情聚焦冒险、成长与关系发展。",
            priority=100,
        )
        assert result.to_core_memory_items() == [result.to_core_memory_item()]


class TestBuildInitialCurrentState:
    def test_uses_protagonist_name_and_birth_location(self):
        state = build_initial_current_state(InitConfig(
            fanfic=True,
            work_name="魔兽世界",
            birth_location="北郡修道院",
            story_era="经典旧世",
            protagonist_name="卢西安",
            protagonist_gender="男",
            protagonist_age=18,
        ))

        assert state.scene.scene_id == "scene_001"
        assert state.scene.location == "北郡修道院"
        assert state.scene.participants == ["卢西安"]
        assert state.player.name == "卢西安"
        assert state.player.position == "北郡修道院"
        assert state.player.visible_status == "男性，18岁"
        assert state.world_clock.label == "第1日上午"

    def test_original_world_falls_back_to_world_name_as_location(self):
        state = build_initial_current_state(InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            protagonist_name="卢西安",
        ))

        assert state.scene.location == "艾兰"
        assert state.player.name == "卢西安"


class TestCoreMemoryStore:
    def test_load_missing_returns_none(self, tmp_path):
        store = CoreMemoryStore(tmp_path / "core_memory.json")
        assert store.load() is None

    def test_save_and_load(self, tmp_path):
        store = CoreMemoryStore(tmp_path / "core_memory.json")
        cm = CoreMemory(items=[CoreMemoryItem(id="x", category="world", content="内容")])

        store.save(cm)

        assert json.loads((tmp_path / "core_memory.json").read_text(encoding="utf-8"))["items"][0]["content"] == "内容"
        assert store.load() == cm

    def test_load_required_missing_raises(self, tmp_path):
        store = CoreMemoryStore(tmp_path / "core_memory.json")
        with pytest.raises(RuntimeError):
            store.load_required()


class TestDebugCoreMemoryPreview:
    def test_preview_incomplete_config_returns_validation_errors(self):
        result = preview_core_memory(
            {
                "version": 1,
                "fanfic": True,
                "work_name": "魔兽世界",
                "birth_location": "北郡",
                "story_era": "经典旧世",
                "extra_core_memory_items": [],
            }
        )

        assert result["valid"] is False
        assert result["core_memory"] is None
        assert result["validation_errors"]

    def test_apply_generates_story_direction_before_opening(
        self,
        tmp_path,
        monkeypatch,
    ):
        import app.graph.initialization_graph as core_memory_engine
        from app.graph import initialization_helpers

        calls = []

        class RecordingGateway:
            def run(self, task, input_data, output_schema, **kwargs):
                calls.append((task, input_data))
                if task == "story_direction":
                    return output_schema.model_validate({
                        "content": "剧情聚焦好莱坞经纪公司的经营成长、演员发掘、行业合作与浪漫关系。",
                        "currency_unit": "美元",
                    })
                if task == "opening_scene":
                    return output_schema.model_validate({
                        "scene_text": "清晨，你站在刚租下的经纪公司办公室里。",
                    })
                raise AssertionError(f"unexpected task: {task}")

            def set_trace_context(self, **kwargs):
                pass

        core_store = CoreMemoryStore(tmp_path / "core_memory.json")
        state_path = tmp_path / "current_state.db"
        monkeypatch.setattr(
            initialization_helpers,
            "create_gateway",
            lambda: RecordingGateway(),
        )
        monkeypatch.setattr(
            initialization_helpers,
            "core_memory_store",
            lambda: core_store,
        )
        monkeypatch.setattr(
            initialization_helpers,
            "current_state_store",
            lambda: CurrentStateStore(str(state_path)),
        )

        result = core_memory_engine.apply_core_memory_init({
            "version": 1,
            "fanfic": False,
            "world_name": "好莱坞",
            "world_style": "1990年代娱乐业",
            "protagonist_name": "莱尔",
            "story_expectation": "经营经纪公司，发掘演员并发展事业与浪漫关系。",
            "extra_core_memory_items": [],
        })

        try:
            assert [task for task, _ in calls] == ["story_direction", "opening_scene"]
            opening_core = json.loads(calls[1][1]["core_memory_json"])
            assert any(
                item["id"] == "core_story_direction"
                for item in opening_core["items"]
            )
            assert any(
                item["id"] == "core_currency_system"
                for item in opening_core["items"]
            )
            assert result["current_state"]["resources"]["money_unit"] == "美元"
            assert core_store.load() == CoreMemory.model_validate(result["core_memory"])
        finally:
            PENDING_OPENINGS.pop(result["turn_id"], None)
