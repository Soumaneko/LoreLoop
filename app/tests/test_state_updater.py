import json

from app.core.llm import MockLLM
from app.domain.current_state import (
    CurrentState,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
    StateUpdater,
    WorldClock,
)


def _make_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="北郡修道院",
            time_label="第1日深夜",
            participants=["主角", "米莉", "莎拉"],
            atmosphere="宁静",
        ),
        world_clock=WorldClock(day=1, phase="深夜"),
        player=PlayerState(
            name="主角",
            condition=[],
            position="修道院大厅",
            visible_status="正常",
            intent="了解当前局势",
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
            NPCState(
                name="莎拉",
                role="冒险者",
                location="酒馆角落",
                condition=[],
                attitude_to_player="好奇",
                current_intent="观察主角",
            ),
        ],
        resources=ResourcesState(
            inventory=["旧地图", "干粮"],
            money=50,
            key_items=[],
            fixed_assets=[],
            temporary_assets=["租来的马"],
        ),
    )


def _build_updated_state_json(overrides: dict | None = None) -> str:
    state = _make_state()
    data = json.loads(state.model_dump_json())
    if overrides:
        _deep_update(data, overrides)
    return json.dumps(data, ensure_ascii=False)


def _build_updated_state_json_without_scene_id(overrides: dict | None = None) -> str:
    state = _make_state()
    data = json.loads(state.model_dump_json())
    data["scene"].pop("scene_id", None)
    if overrides:
        _deep_update(data, overrides)
    return json.dumps(data, ensure_ascii=False)


def _deep_update(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


class TestStateUpdaterNoChange:
    def test_unchanged_state_returns_same(self):
        state = _make_state()
        mock = MockLLM()
        mock.set_responses([state.model_dump_json()])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角环顾四周，一切如常。")

        assert result == state

    def test_location_unchanged_scene_id_preserved(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "scene": {"atmosphere": "紧张", "scene_id": "scene_001"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "酒馆气氛突然变得紧张。")

        assert result.scene.atmosphere == "紧张"
        assert result.scene.location == "北郡修道院"
        assert result.scene.scene_id == "scene_001"
        assert result.world_clock == state.world_clock

    def test_world_clock_advances_from_program_suggestion(self):
        state = _make_state()
        updated_json = _build_updated_state_json_without_scene_id({
            "world_clock_suggestion": {
                "advance_phases": 1,
                "reason": "剧情明确进入下一时间段",
            }
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "谈话持续到天色更深。")

        assert result.world_clock.day == 2
        assert result.world_clock.phase == "凌晨"


class TestStateUpdaterPlayerChange:
    def test_player_position_changes(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "player": {"position": "修道院门口"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角推开大门，走出了修道院。")

        assert result.player.position == "修道院门口"
        assert result.scene.location == state.scene.location

    def test_player_condition_changes(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "player": {"condition": ["轻伤"]},
            "resources": {"inventory": ["旧地图", "干粮", "治疗药水"]},
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角在一次冒险中受了些轻伤，但找到了治疗药水。")

        assert "轻伤" in result.player.condition
        assert "治疗药水" in result.resources.inventory

    def test_player_intent_updates(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "player": {"intent": "调查失踪事件"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角决定先调查修道院附近的失踪事件。")

        assert result.player.intent == "调查失踪事件"


class TestStateUpdaterNPCChange:
    def test_npc_attitude_changes(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "npcs": [
                {
                    "name": "米莉",
                    "role": "酒馆老板",
                    "location": "酒馆吧台",
                    "condition": [],
                    "attitude_to_player": "感激",
                    "current_intent": "准备报酬",
                },
                {
                    "name": "莎拉",
                    "role": "冒险者",
                    "location": "酒馆角落",
                    "condition": [],
                    "attitude_to_player": "好奇",
                    "current_intent": "观察主角",
                },
            ]
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角帮米莉赶走了闹事的醉汉，米莉感激地表示要准备报酬。")

        milly = next(n for n in result.npcs if n.name == "米莉")
        assert milly.attitude_to_player == "感激"
        assert milly.current_intent == "准备报酬"
        assert len(result.npcs) == 2

    def test_new_npc_added(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "npcs": [
                {
                    "name": "米莉",
                    "role": "酒馆老板",
                    "location": "酒馆吧台",
                    "condition": [],
                    "attitude_to_player": "友好",
                    "current_intent": "招待客人",
                },
                {
                    "name": "莎拉",
                    "role": "冒险者",
                    "location": "酒馆角落",
                    "condition": [],
                    "attitude_to_player": "好奇",
                    "current_intent": "观察主角",
                },
                {
                    "name": "醉汉",
                    "role": "捣乱者",
                    "location": "酒馆大厅",
                    "condition": ["酒醉"],
                    "attitude_to_player": "敌意",
                    "current_intent": "挑衅主角",
                },
            ]
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "一个浑身酒气的壮汉摇摇晃晃地走向主角。")

        assert len(result.npcs) == 3
        drunk = next(n for n in result.npcs if n.name == "醉汉")
        assert drunk.role == "捣乱者"
        assert "酒醉" in drunk.condition

    def test_npc_leaves(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "npcs": [
                {
                    "name": "米莉",
                    "role": "酒馆老板",
                    "location": "酒馆吧台",
                    "condition": [],
                    "attitude_to_player": "友好",
                    "current_intent": "招待客人",
                },
            ]
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "莎拉站起身来，默默离开了酒馆。")

        assert len(result.npcs) == 1
        assert result.npcs[0].name == "米莉"


class TestStateUpdaterSceneChange:
    def test_scene_location_changes(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "scene": {"location": "北郡森林"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角离开了修道院，进入了北郡森林。")

        assert result.scene.location == "北郡森林"
        assert result.scene.scene_id == "scene_002"

    def test_scene_atmosphere_changes(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "scene": {"atmosphere": "紧张"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "突然传来一声尖叫，酒馆的客人们都紧张起来。")

        assert result.scene.atmosphere == "紧张"


class TestStateUpdaterResourcesChange:
    def test_item_added_to_inventory(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "resources": {"inventory": ["旧地图", "干粮"]}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角吃掉了干粮。")

        assert len(result.resources.inventory) == 2

    def test_money_changes(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "resources": {"money": 30}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角付了20枚金币买了些补给。")

        assert result.resources.money == 30


class TestStateUpdaterConsistency:
    def test_player_name_unchanged(self):
        state = _make_state()
        mock = MockLLM()
        mock.set_responses([state.model_dump_json()])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "一段无关的剧情。")

        assert result.player.name == "主角"

    def test_scene_id_preserved_when_location_same(self):
        state = _make_state()
        mock = MockLLM()
        mock.set_responses([state.model_dump_json()])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "一段无关的剧情。")

        assert result.scene.scene_id == "scene_001"


class TestSceneIdAutoIncrement:
    def test_accepts_llm_output_without_scene_id(self):
        state = _make_state()
        updated_json = _build_updated_state_json_without_scene_id({
            "scene": {"atmosphere": "疏离"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "酒馆里的交谈变得冷淡，气氛疏离。")

        assert result.scene.scene_id == "scene_001"
        assert result.scene.atmosphere == "疏离"

    def test_increments_on_location_change(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "scene": {"location": "北郡森林"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角走进了北郡森林。")

        assert result.scene.scene_id == "scene_002"

    def test_preserved_when_location_same(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "player": {"position": "酒馆门口"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角走到酒馆门口。")

        assert result.scene.scene_id == "scene_001"

    def test_ignores_llm_scene_id_output(self):
        state = _make_state()
        updated_json = _build_updated_state_json({
            "scene": {"scene_id": "scene_999"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "酒馆里一切如常。")

        assert result.scene.scene_id == "scene_001"

    def test_increment_preserves_padding(self):
        state = _make_state()
        state.scene.scene_id = "scene_009"
        updated_json = _build_updated_state_json({
            "scene": {"location": "北郡森林"}
        })
        mock = MockLLM()
        mock.set_responses([updated_json])

        updater = StateUpdater(mock.build_gateway())
        result = updater.update(state, "主角离开修道院。")

        assert result.scene.scene_id == "scene_010"
