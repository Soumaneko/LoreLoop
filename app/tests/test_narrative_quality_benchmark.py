from scripts.benchmark_narrative_quality import (
    DEFAULT_FIXTURES,
    check_invariants,
    load_fixtures,
)


def test_active_benchmark_contract():
    cases = load_fixtures(DEFAULT_FIXTURES)
    assert len(cases) == 10
    case = cases[0]
    judgment = case.action_judgments[0]
    result = {
        "director_reason": None,
        "action_resolutions": [{
            "action_id": judgment.action_id,
            "outcome_band": judgment.outcome_band,
            "resolved_fact": "行动结果已经落实。",
        }],
        "scene_stopping_point": "现场仍等待玩家回应。",
        "planned_events": [{
            "event_id": "e1",
            "event_fact": "行动完成，世界作出回应。",
            "linked_outcome_ids": [judgment.action_id],
            "involved_entities": [],
        }],
        "scene_reasoning": "落实结果并停在开放局面。",
        "scene_text": "行动完成后，现场出现了新的变化。",
        "closing_prompt": "局势仍在变化，你会怎么做？",
        "next_action_directions": [],
    }
    assert check_invariants(case, result).passed
