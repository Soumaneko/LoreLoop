import json

import pytest
from pydantic import ValidationError

from app.domain.core_memory import (
    CoreMemory,
    CoreMemoryItem,
    CoreMemoryStore,
    InitConfig,
    build_core_memory,
    build_initial_current_state,
)
from app.debug_server import _preview_core_memory


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
                magic_level="普通",
                protagonist_name="",
            )

    def test_ability_limit_is_three(self):
        with pytest.raises(ValidationError):
            InitConfig(
                fanfic=False,
                world_name="艾兰",
                world_style="西幻",
                magic_level="普通",
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
            magic_level="高魔",
            protagonist_name="卢西安",
        ))

        world = next(item for item in cm.items if item.id == "core_world_identity")
        assert world.content == "当前世界名为「艾兰」，是一个西幻风格的原创世界。魔力等级为高魔。"

    def test_player_identity_optional_age_and_background(self):
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            magic_level="普通",
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
            magic_level="普通",
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
            magic_level="普通",
            protagonist_name="卢西安",
        ))

        assert all(item.id != "core_player_abilities" for item in cm.items)

    def test_tone_and_extra_items_are_appended(self):
        extra = CoreMemoryItem(id="extra_rule", category="world", content="额外规则", priority=80)
        cm = build_core_memory(InitConfig(
            fanfic=False,
            world_name="艾兰",
            world_style="西幻",
            magic_level="普通",
            protagonist_name="卢西安",
            narrative_tone="冷峻写实",
            extra_core_memory_items=[extra],
        ))

        assert next(item for item in cm.items if item.id == "core_tone").content == "冷峻写实"
        assert cm.items[-1] == extra

    def test_empty_core_memory_still_valid(self):
        cm = CoreMemory(items=[])
        assert cm.items == []


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
            magic_level="普通",
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
        result = _preview_core_memory(
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
