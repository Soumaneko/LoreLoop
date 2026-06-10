from app.domain.current_state import (
    CurrentState,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
)


class TestSceneState:
    def test_required_scene_fields(self):
        state = SceneState(
            scene_id="scene_001",
            location="修道院门口",
            time_label="清晨",
            participants=["主角", "米莉"],
        )

        assert state.atmosphere is None
        assert state.participants == ["主角", "米莉"]


class TestPlayerState:
    def test_defaults_are_empty_or_none(self):
        state = PlayerState(name="卢西安")

        assert state.condition == []
        assert state.position is None
        assert state.visible_status is None
        assert state.intent is None


class TestNPCState:
    def test_current_npc_state(self):
        state = NPCState(
            name="米莉",
            role="被救助者",
            condition=["疲惫"],
            attitude_to_player="感激主角",
            current_intent="准备回葡萄园",
        )

        assert state.location is None
        assert state.condition == ["疲惫"]


class TestResourcesState:
    def test_resource_defaults_are_isolated(self):
        r1 = ResourcesState()
        r2 = ResourcesState()

        r1.inventory.append("匕首")
        r1.key_items.append("鹰徽章")
        r1.fixed_assets.append("葡萄园")
        r1.temporary_assets.append("临时通行证")

        assert r2.inventory == []
        assert r2.key_items == []
        assert r2.fixed_assets == []
        assert r2.temporary_assets == []

    def test_money_can_be_none_or_int(self):
        assert ResourcesState().money is None
        assert ResourcesState(money=12).money == 12


class TestCurrentState:
    def test_combines_state_parts(self):
        state = CurrentState(
            scene=SceneState(
                scene_id="scene_001",
                location="修道院门口",
                time_label="清晨",
                participants=["卢西安", "米莉", "莎拉"],
                atmosphere="紧张但暂时安全",
            ),
            player=PlayerState(
                name="卢西安",
                condition=["疲惫"],
                position="修道院门口",
                visible_status="衣袍沾着泥水",
                intent="安顿米莉并和莎拉确认明日安排",
            ),
            npcs=[
                NPCState(
                    name="莎拉",
                    attitude_to_player="仍警惕但开始认可主角",
                    current_intent="关心明日安排",
                )
            ],
            resources=ResourcesState(key_items=["鹰徽章"]),
        )

        assert state.scene.scene_id == "scene_001"
        assert state.player.name == "卢西安"
        assert state.npcs[0].name == "莎拉"
        assert state.resources.key_items == ["鹰徽章"]
