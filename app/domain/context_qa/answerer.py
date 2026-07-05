import json
from typing import cast

from pydantic import model_validator

from app.core.llm import LLMGateway
from app.domain.context_qa.schemas import (
    ContextAnswer,
    ContextAnswerInput,
    ContextAnswerSet,
    ContextQuestionForAnswer,
)
from app.domain.director_event.views import project_director_event
from app.domain.views import (
    project_current_state,
    project_retrieved_memories,
    project_scene_summaries,
)


class ContextAnswerAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        input_data: ContextAnswerInput,
        *,
        trace_id: str | None = None,
    ) -> ContextAnswerSet:
        expected_question_ids = [
            question.question_id
            for question in input_data.questions.questions
        ]

        class RuntimeContextAnswerSet(ContextAnswerSet):
            answers: list[ContextAnswer]

            @model_validator(mode="after")
            def _validate_answer_ids_match_questions(self):
                actual_ids = [answer.question_id for answer in self.answers]
                unknown = [
                    question_id
                    for question_id in actual_ids
                    if question_id not in expected_question_ids
                ]
                if unknown:
                    raise ValueError(f"answers reference unknown question_id: {unknown}")
                missing = [
                    question_id
                    for question_id in expected_question_ids
                    if question_id not in actual_ids
                ]
                if missing:
                    raise ValueError(f"answers missing question_id: {missing}")
                return self

        questions = [
            ContextQuestionForAnswer(
                question_id=question.question_id,
                question=question.question,
                purpose=question.purpose,
            ).model_dump(mode="json")
            for question in input_data.questions.questions
        ]
        schema = RuntimeContextAnswerSet.model_json_schema()
        return cast(
            ContextAnswerSet,
            self.llm.run(
                task="context_answers",
                input_data={
                    "core_memory_json": input_data.core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(input_data.current_state),
                    "director_event_json": project_director_event(
                        input_data.director_event,
                        recent_archived_event=input_data.recent_archived_director_event,
                    ),
                    "scene_summaries_json": project_scene_summaries(input_data.scene_summaries),
                    "retrieved_memories_json": project_retrieved_memories(
                        input_data.retrieved_memories
                    ),
                    "questions_json": json.dumps(
                        {
                            "turn_focus": input_data.questions.turn_focus,
                            "questions": questions,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=RuntimeContextAnswerSet,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
