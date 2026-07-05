import json

from app.domain.context_qa.schemas import ContextAnswerSet


def project_context_qa(answers: ContextAnswerSet) -> str:
    data = [
        {
            "question_id": answer.question_id,
            "question": answer.question,
            "answer": answer.answer,
            "support_status": answer.support_status,
        }
        for answer in answers.answers
        if answer.support_status != "unsupported_by_memory_or_summary"
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)
