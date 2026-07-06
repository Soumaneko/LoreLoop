import json

from app.domain.context_facts.schemas import ContextFactSet


def project_context_facts(context_facts: ContextFactSet) -> str:
    data = [
        {
            "fact_id": fact.fact_id,
            "fact": fact.fact,
        }
        for fact in context_facts.facts
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)
