import json

from .schemas import ActionJudgment


def project_action_judgments(judgments: list[ActionJudgment]) -> str:
    return json.dumps(
        [judgment.model_dump(mode="json") for judgment in judgments],
        ensure_ascii=False,
        indent=2,
    )
