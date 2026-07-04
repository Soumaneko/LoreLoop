import pytest

from app.domain.context_qa.schemas import ContextQuestionSet, RetrievalHint


def test_retrieval_hint_fills_normalized_text():
    hint = RetrievalHint(
        text="顾晚棠",
        kind="character",
        priority="must",
        reason="本回合需要判断她对玩家条件的反应边界。",
    )

    assert hint.normalized == "顾晚棠"


def test_context_question_set_accepts_questions_and_generation_hints():
    result = ContextQuestionSet(
        turn_focus="判断顾晚棠如何回应萧云痕提出庇护与约会报酬。",
        retrieval_hints=[
            RetrievalHint(
                text="顾晚棠",
                kind="character",
                priority="must",
                reason="核心 NPC。",
            ),
            RetrievalHint(
                text="盐帮追兵",
                kind="organization",
                priority="useful",
                reason="当前威胁来源。",
            ),
        ],
        questions=[
            {
                "question_id": "q1",
                "question": "顾晚棠基于伤势、性格和当前信任程度，合理反应边界是什么？",
                "purpose": "npc_reaction",
                "priority": "must",
                "hint_texts": ["顾晚棠"],
            },
            {
                "question_id": "q2",
                "question": "盐帮追兵对当前房间安全造成哪些仍未解除的压力？",
                "purpose": "continuity",
                "priority": "useful",
                "hint_texts": ["盐帮追兵"],
            },
        ],
    )

    assert len(result.questions) == 2
    assert result.retrieval_hints[0].priority == "must"


def test_context_question_set_rejects_unknown_hint_reference():
    with pytest.raises(ValueError, match="unknown hints"):
        ContextQuestionSet(
            turn_focus="测试",
            retrieval_hints=[],
            questions=[
                {
                    "question_id": "q1",
                    "question": "需要哪些旧线索？",
                    "purpose": "memory_hook",
                    "hint_texts": ["不存在"],
                }
            ],
        )


def test_context_question_set_rejects_duplicate_questions_and_hints():
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

    with pytest.raises(ValueError, match="retrieval_hints"):
        ContextQuestionSet(
            turn_focus="测试",
            retrieval_hints=[
                RetrievalHint(text="顾晚棠", kind="character", reason="A"),
                RetrievalHint(text="顾阁主", normalized="顾晚棠", kind="character", reason="B"),
            ],
        )
