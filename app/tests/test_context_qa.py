import pytest

from app.domain.context_qa.questioner import ContextQuestionAgent
from app.domain.context_qa.schemas import (
    ContextQuestionInput,
    ContextQuestionSet,
    RetrievalHint,
)
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.entity.schemas import EntityProfile
from app.domain.entity.store import EntityStore
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.workflows.select_context_profiles import select_context_profiles


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="客栈上房",
            participants=["萧云痕", "顾晚棠"],
        ),
        player=PlayerState(name="萧云痕", position="床沿边"),
        npcs=[NPCState(name="顾晚棠", location="客栈上房")],
        resources=ResourcesState(inventory=[InventoryItem(name="麂皮褡裢")]),
    )


def _core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="core_world",
            category="world",
            content="当前世界为武侠原创世界。",
            priority=100,
        )
    ])


def test_retrieval_hint_fills_normalized_text():
    hint = RetrievalHint(
        text="顾晚棠",
        kind="character",
        reason="本回合需要判断她对玩家条件的反应边界。",
    )

    assert hint.normalized == "顾晚棠"


def test_context_question_set_accepts_questions_and_generation_hints():
    result = ContextQuestionSet(
        turn_focus="判断顾晚棠如何回应萧云痕提出庇护与约会报酬。",
        questions=[
            {
                "question_id": "q1",
                "question": "顾晚棠基于伤势、性格和当前信任程度，合理反应边界是什么？",
                "purpose": "npc_reaction",
                "retrieval_hints": [
                    {
                        "text": "顾晚棠",
                        "kind": "character",
                        "reason": "核心 NPC。",
                    }
                ],
            },
            {
                "question_id": "q2",
                "question": "盐帮追兵对当前房间安全造成哪些仍未解除的压力？",
                "purpose": "continuity",
                "retrieval_hints": [
                    {
                        "text": "盐帮追兵",
                        "kind": "organization",
                        "reason": "当前威胁来源。",
                    }
                ],
            },
        ],
    )

    assert len(result.questions) == 2
    assert result.questions[0].retrieval_hints[0].text == "顾晚棠"


def test_context_question_set_rejects_duplicate_questions():
    with pytest.raises(ValueError, match="question_id"):
        ContextQuestionSet(
            turn_focus="测试",
            questions=[
                {
                    "question_id": "q1",
                    "question": "问题一？",
                    "purpose": "continuity",
                },
                {
                    "question_id": "q1",
                    "question": "问题二？",
                    "purpose": "continuity",
                },
            ],
        )


def test_context_question_agent_projects_profiles_to_llm():
    class SpyGateway:
        def __init__(self):
            self.input_data = None

        def run(self, **kwargs):
            self.input_data = kwargs["input_data"]
            return kwargs["output_schema"].model_validate({
                "turn_focus": "萧云痕试图安置顾晚棠。",
                "questions": [],
            })

    gateway = SpyGateway()
    result = ContextQuestionAgent(gateway).generate(
        ContextQuestionInput(
            user_input="我让顾晚棠在这里躲一阵子。",
            current_state=_state(),
            core_memory=_core_memory(),
            entity_profiles=[
                EntityProfile(
                    entity_type="item",
                    name="麂皮褡裢",
                    summary="萧云痕随身携带的褡裢。",
                ).semantic_view()
            ],
            npc_profiles=[
                NPCProfile(
                    name="顾晚棠",
                    role="万象阁阁主",
                    attitude_to_player="部分信任但仍戒备",
                ).director_view()
            ],
        )
    )

    assert result.turn_focus == "萧云痕试图安置顾晚棠。"
    assert "scene_001" in gateway.input_data["current_state_json"]
    assert "麂皮褡裢" in gateway.input_data["entity_profiles_json"]
    assert "顾晚棠" in gateway.input_data["npc_profiles_json"]


def test_select_context_profiles_matches_state_and_user_input(tmp_path):
    entity_store = EntityStore(str(tmp_path / "entities.db"))
    npc_store = NPCProfileStore(str(tmp_path / "npcs.db"))
    try:
        entity_store.upsert(
            EntityProfile(
                entity_type="item",
                name="麂皮褡裢",
                aliases=["褡裢"],
                summary="萧云痕随身携带的包裹。",
            )
        )
        entity_store.upsert(
            EntityProfile(
                entity_type="organization",
                name="盐帮",
                summary="正在追捕顾晚棠的组织。",
            )
        )
        npc_store.upsert(
            NPCProfile(
                name="顾晚棠",
                aliases=["顾阁主"],
                role="万象阁阁主",
            )
        )

        result = select_context_profiles(
            user_input="外面都是盐帮追兵，我让顾阁主在这里躲一阵。",
            current_state=_state(),
            entity_store=entity_store,
            npc_store=npc_store,
        )
    finally:
        entity_store.close()
        npc_store.close()

    assert {profile.name for profile in result.entity_profiles} >= {"麂皮褡裢", "盐帮"}
    assert [profile.name for profile in result.npc_profiles] == ["顾晚棠"]
    assert {anchor.text for anchor in result.anchors} >= {"顾晚棠", "麂皮褡裢", "盐帮"}
