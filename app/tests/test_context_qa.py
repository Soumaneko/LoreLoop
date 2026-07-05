import json

import pytest

from app.domain.context_qa.questioner import ContextQuestionAgent
from app.domain.context_qa.schemas import (
    ContextAnswerInput,
    ContextAnswerSet,
    ContextQuestionInput,
    ContextQuestionSet,
    RetrievalHint,
)
from app.domain.context_qa.views import project_context_qa
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
from app.domain.recall_memory.schemas import Memory
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.scene_summary.schemas import SceneSummary
from app.workflows.answer_context_questions import retrieve_context_question_memories
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
                "questions": [
                    {
                        "question": "顾晚棠近期是否仍在戒备萧云痕？",
                        "purpose": "npc_reaction",
                    }
                ],
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
    assert result.questions[0].question_id == "q1"
    assert "scene_001" in gateway.input_data["current_state_json"]
    assert "麂皮褡裢" in gateway.input_data["entity_profiles_json"]
    assert "顾晚棠" in gateway.input_data["npc_profiles_json"]
    schema = json.loads(gateway.input_data["output_schema_json"])
    question_def_name = next(
        name
        for name in schema["$defs"]
        if name.endswith("ContextQuestionDraft")
    )
    assert "question_id" not in schema["$defs"][question_def_name]["properties"]


def test_context_question_memory_retrieval_uses_question_hints():
    questions = ContextQuestionSet(
        turn_focus="判断盐帮威胁是否仍影响顾晚棠。",
        questions=[
            {
                "question_id": "q1",
                "question": "盐帮追兵对当前安全造成哪些压力？",
                "purpose": "continuity",
                "retrieval_hints": [
                    {
                        "text": "盐帮",
                        "kind": "organization",
                        "reason": "需要检索追兵压力。",
                    }
                ],
            }
        ],
    )
    with MemoryStore(":memory:") as store:
        store.save_one_returning_id(
            Memory(
                memory_kind="open_thread",
                evidence_mode="observed",
                subject="盐帮追兵",
                content="盐帮追兵仍在客栈外搜寻顾晚棠。",
                entities=["盐帮", "顾晚棠"],
                tags=["追兵"],
                salience=0.9,
            )
        )

        result = retrieve_context_question_memories(questions, store)

    assert result.total_returned == 1
    assert result.candidates[0].subject == "盐帮追兵"
    assert "盐帮" in result.candidates[0].matched_anchors


def test_context_answer_agent_projects_required_inputs():
    from app.domain.context_qa.answerer import ContextAnswerAgent

    class SpyGateway:
        def __init__(self):
            self.input_data = None

        def run(self, **kwargs):
            self.input_data = kwargs["input_data"]
            return kwargs["output_schema"].model_validate({
                "turn_focus": "判断盐帮威胁是否仍影响顾晚棠。",
                "answers": [
                    {
                        "question_id": "q1",
                        "question": "盐帮追兵对当前安全造成哪些压力？",
                        "answer": "记忆支持盐帮追兵仍在客栈外搜寻顾晚棠。",
                        "support_status": "supported",
                        "memory_indices": [0],
                        "scene_summary_indices": [],
                    }
                ],
            })

    questions = ContextQuestionSet(
        turn_focus="判断盐帮威胁是否仍影响顾晚棠。",
        questions=[
            {
                "question_id": "q1",
                "question": "盐帮追兵对当前安全造成哪些压力？",
                "purpose": "continuity",
            }
        ],
    )
    gateway = SpyGateway()
    result = ContextAnswerAgent(gateway).generate(
        ContextAnswerInput(
            core_memory=_core_memory(),
            current_state=_state(),
            scene_summaries=[
                SceneSummary(
                    level=1,
                    content="顾晚棠曾被盐帮追捕。",
                    turn_start=1,
                    turn_end=2,
                )
            ],
            retrieved_memories=CandidateRetrievalResult(
                candidates=[
                    MemoryCandidate(
                        memory_id="m1",
                        memory_kind="open_thread",
                        evidence_mode="observed",
                        subject="盐帮追兵",
                        content="盐帮追兵仍在客栈外搜寻顾晚棠。",
                        entities=["盐帮", "顾晚棠"],
                        salience=0.9,
                        score=1.0,
                    )
                ],
                total_scanned=1,
                total_returned=1,
            ),
            questions=questions,
        )
    )

    assert result.answers[0].question_id == "q1"
    assert "core_world" in gateway.input_data["core_memory_json"]
    assert "scene_001" in gateway.input_data["current_state_json"]
    assert "盐帮追兵" in gateway.input_data["retrieved_memories_json"]
    assert "顾晚棠曾被盐帮追捕" in gateway.input_data["scene_summaries_json"]
    assert "q1" in gateway.input_data["questions_json"]


def test_context_qa_projection_omits_unsupported_answers():
    answers = ContextAnswerSet(
        turn_focus="判断顾晚棠是否有脱身计划。",
        answers=[
            {
                "question_id": "q1",
                "question": "顾晚棠过去是否透露过脱身计划？",
                "answer": "未知",
                "support_status": "unsupported_by_memory_or_summary",
            },
            {
                "question_id": "q2",
                "question": "三日庇护之约是什么？",
                "answer": "萧云痕承诺庇护顾晚棠三日。",
                "support_status": "supported",
            },
        ],
    )

    projected = json.loads(project_context_qa(answers))

    assert [item["question_id"] for item in projected] == ["q2"]
    assert "脱身计划" not in json.dumps(projected, ensure_ascii=False)


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
