import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory

from .changes import (
    StateChangeError,
    StateChangeSet,
    apply_state_changes,
)
from .schemas import CurrentState


_MAX_SEMANTIC_RETRIES = 2


class StateUpdater:
    """Compile Scene Facts into typed changes and apply them deterministically."""

    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_change_set = StateChangeSet()

    def update(
        self,
        current_state: CurrentState,
        scene_facts: str,
        *,
        core_memory: CoreMemory,
        npc_aliases: dict[str, list[str]] | None = None,
        trace_id: str | None = None,
    ) -> CurrentState:
        schema = StateChangeSet.model_json_schema()
        valid_fact_ids = _scene_fact_ids(scene_facts)
        validation_feedback = "无；这是首次输出。"

        for attempt in range(_MAX_SEMANTIC_RETRIES + 1):
            change_set = cast(
                StateChangeSet,
                self.llm.run(
                    task="state_update",
                    input_data={
                        "core_memory_json": core_memory.model_dump_json(indent=2),
                        "current_state_json": current_state.model_dump_json(indent=2),
                        "scene_facts_json": scene_facts,
                        "validation_feedback": validation_feedback,
                        "output_schema_json": json.dumps(
                            schema,
                            ensure_ascii=False,
                            indent=2,
                        ),
                    },
                    output_schema=StateChangeSet,
                    trace_id=trace_id,
                ),
            )
            try:
                _validate_evidence(change_set, valid_fact_ids)
                updated_state = apply_state_changes(
                    current_state,
                    change_set,
                    npc_aliases=npc_aliases,
                )
            except StateChangeError as exc:
                if attempt == _MAX_SEMANTIC_RETRIES:
                    raise
                validation_feedback = (
                    f"上一次 StateChangeSet 无法应用：{exc}。"
                    "请仅输出由 Scene Facts 支持、且能从 CurrentState 合法应用的操作。"
                )
                continue

            self.last_change_set = change_set
            return updated_state

        raise RuntimeError("unreachable state update retry state")


def _scene_fact_ids(scene_facts: str) -> set[str]:
    try:
        data = json.loads(scene_facts)
    except (json.JSONDecodeError, TypeError):
        return set()
    return {
        str(item.get("fact_id"))
        for item in data.get("facts", [])
        if isinstance(item, dict) and item.get("fact_id")
    }


def _validate_evidence(
    change_set: StateChangeSet,
    valid_fact_ids: set[str],
) -> None:
    for operation in change_set.operations:
        unknown = set(operation.evidence_fact_ids) - valid_fact_ids
        if unknown:
            raise StateChangeError(
                f"{operation.operation_id} references unknown Scene Fact IDs: "
                f"{sorted(unknown)}"
            )
