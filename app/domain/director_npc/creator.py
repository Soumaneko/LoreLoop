import json
from dataclasses import dataclass
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import display_schema_json
from app.domain.core_memory.schemas import CoreMemory
from app.domain.creative_brief import CreativeBrief
from app.domain.director_npc.schemas import (
    ABILITY_DESCRIPTION_ACCEPT_LIMIT,
    ABILITY_DESCRIPTION_SOFT_LIMIT,
    ABILITY_NAME_ACCEPT_LIMIT,
    PERSONALITY_TRAIT_ACCEPT_LIMIT,
    PERSONALITY_TRAIT_SOFT_LIMIT,
    DirectorNPCCreateOutput,
    DirectorNPCCreateOutputWire,
    DirectorNPCBehavior,
    DirectorNPCDraft,
    DirectorNPCShell,
    KnownAbility,
    NPCCreationContext,
)


@dataclass(frozen=True)
class DirectorNPCCreationWarning:
    code: str
    message: str


class DirectorNPCCreator:
    def __init__(self, llm: LLMGateway):
        self._llm = llm
        self.last_warnings: list[DirectorNPCCreationWarning] = []

    def create(
        self,
        *,
        core_memory: CoreMemory,
        existing_npcs: list,
        reserved_npcs: list[dict[str, str | None]] | None = None,
        creation_context: NPCCreationContext,
        settings: list,
        creative_brief: CreativeBrief,
        protagonist_name: str,
        turn_index: int,
        trace_id: str | None = None,
        guidance: str | None = None,
        recent_creations: list[dict] | None = None,
    ) -> DirectorNPCDraft:
        existing_npc_views = []
        seen_names = set()
        for npc in existing_npcs:
            if npc.name in seen_names:
                continue
            existing_npc_views.append(_existing_npc_view(npc))
            seen_names.add(npc.name)
        for npc in reserved_npcs or []:
            name = npc.get("name")
            if not name or name in seen_names:
                continue
            existing_npc_views.append(_existing_npc_view(npc))
            seen_names.add(name)

        attempt_outputs: dict[int, DirectorNPCCreateOutput] = {}
        attempt_warnings: dict[int, list[DirectorNPCCreationWarning]] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(
            output: DirectorNPCCreateOutputWire,
        ) -> None:
            warnings: list[DirectorNPCCreationWarning] = []
            personality = output.behavior.personality_trait.strip()

            if current_attempt == 1:
                # First attempt: everything within the Accept Limit is taken
                # as-is; any field above A triggers the single feedback retry
                # asking the model to return to the prompt target H.
                retry_errors: list[str] = []
                if len(personality) > PERSONALITY_TRAIT_ACCEPT_LIMIT:
                    retry_errors.append(
                        f"behavior.personality_trait length "
                        f"{len(personality)} exceeds accept limit "
                        f"{PERSONALITY_TRAIT_ACCEPT_LIMIT}; return to the "
                        "prompt target length"
                    )
                for index, ability in enumerate(output.shell.known_abilities):
                    if len(ability.name) > ABILITY_NAME_ACCEPT_LIMIT:
                        retry_errors.append(
                            f"shell.known_abilities[{index}].name length "
                            f"{len(ability.name)} exceeds accept limit "
                            f"{ABILITY_NAME_ACCEPT_LIMIT}; return to the "
                            "prompt target length"
                        )
                    if (
                        len(ability.description)
                        > ABILITY_DESCRIPTION_ACCEPT_LIMIT
                    ):
                        retry_errors.append(
                            f"shell.known_abilities[{index}].description "
                            f"length {len(ability.description)} exceeds "
                            f"accept limit "
                            f"{ABILITY_DESCRIPTION_ACCEPT_LIMIT}; return to "
                            "the prompt target length"
                        )
                if retry_errors:
                    raise ValueError("; ".join(retry_errors))
                attempt_outputs[current_attempt] = (
                    DirectorNPCCreateOutput.model_validate(
                        output.model_dump()
                    )
                )
                attempt_warnings[current_attempt] = warnings
                return

            # Second attempt (retries exhausted): soft_text fields are kept
            # up to S and truncated above it; strict_name items that still
            # exceed A are dropped whole (never truncated).
            if len(personality) > PERSONALITY_TRAIT_SOFT_LIMIT:
                warnings.append(DirectorNPCCreationWarning(
                    code="director_npc_truncated",
                    message=(
                        "personality_trait exceeded the salvage limit "
                        f"{PERSONALITY_TRAIT_SOFT_LIMIT} and was truncated."
                    ),
                ))
                personality = personality[:PERSONALITY_TRAIT_SOFT_LIMIT]

            kept_abilities = []
            for index, ability in enumerate(output.shell.known_abilities):
                if len(ability.name) > ABILITY_NAME_ACCEPT_LIMIT:
                    warnings.append(DirectorNPCCreationWarning(
                        code="director_npc_ability_dropped",
                        message=(
                            f"known_abilities[{index}] dropped: name length "
                            f"{len(ability.name)} exceeds accept limit "
                            f"{ABILITY_NAME_ACCEPT_LIMIT} after retries"
                        ),
                    ))
                    continue
                description = ability.description
                if len(description) > ABILITY_DESCRIPTION_SOFT_LIMIT:
                    warnings.append(DirectorNPCCreationWarning(
                        code="director_npc_truncated",
                        message=(
                            f"known_abilities[{index}].description exceeded "
                            "the salvage limit "
                            f"{ABILITY_DESCRIPTION_SOFT_LIMIT} and was "
                            "truncated."
                        ),
                    ))
                    description = description[:ABILITY_DESCRIPTION_SOFT_LIMIT]
                kept_abilities.append({
                    "name": ability.name,
                    "description": description,
                })

            attempt_outputs[current_attempt] = DirectorNPCCreateOutput(
                creation_reason=output.creation_reason,
                identity=output.identity,
                behavior=DirectorNPCBehavior(
                    conflict_style=output.behavior.conflict_style,
                    social_style=output.behavior.social_style,
                    personality_trait=personality,
                ),
                shell=DirectorNPCShell(
                    known_abilities=[
                        KnownAbility(**ability) for ability in kept_abilities
                    ],
                    appearance=output.shell.appearance,
                    name=output.shell.name,
                ),
            )
            attempt_warnings[current_attempt] = warnings

        try:
            wire_result = cast(
                DirectorNPCCreateOutputWire,
                self._llm.run(
                    task="director_npc_create",
                    input_data={
                        "core_memory_json": core_memory.model_dump_json(indent=2),
                        "existing_npcs_json": json.dumps(
                            existing_npc_views,
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "protagonist_name": protagonist_name,
                        "turn_index": turn_index,
                        "creation_context_json": creation_context.model_dump_json(
                            indent=2
                        ),
                        "settings_json": json.dumps(
                            [setting.director_view() for setting in settings],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "creative_brief_json": creative_brief.model_dump_json(
                            exclude_none=True,
                            indent=2,
                        ),
                        "director_guidance": guidance or "",
                        "batch_recent_creations_json": json.dumps(
                            recent_creations or [],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "output_schema_json": display_schema_json(
                            DirectorNPCCreateOutput,
                            max_lengths={
                                ("behavior", "personality_trait"): 40,
                                ("shell", "known_abilities", "name"): 20,
                                (
                                    "shell",
                                    "known_abilities",
                                    "description",
                                ): 120,
                            },
                        ),
                    },
                    output_schema=DirectorNPCCreateOutputWire,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    temperature=0.9,
                    trace_id=trace_id,
                ),
            )
        except (
            LLMOutputValidationError,
            LLMOutputParseError,
        ) as exc:
            # 原子产品：identity/behavior/shell 任一必填结构缺失时不拼装
            # “半个 NPC”。仅当某次尝试产出了完整合法结果时才降级采用。
            for attempt in (2, 1):
                result = attempt_outputs.get(attempt)
                if result is not None:
                    self.last_warnings = [
                        *attempt_warnings.get(attempt, []),
                        DirectorNPCCreationWarning(
                            code="director_npc_salvaged",
                            message=(
                                "previous attempt failed validation; a "
                                "complete earlier result was kept: "
                                f"{exc}"
                            ),
                        ),
                    ]
                    return self._to_draft(result, creation_context, turn_index)
            raise

        if current_attempt in attempt_outputs:
            result = attempt_outputs[current_attempt]
            self.last_warnings = list(attempt_warnings.get(current_attempt, []))
        else:
            # StubGateway compatibility: real Gateway always calls the
            # output_validator; stubs may return the wire/domain model
            # directly without invoking it.
            result = DirectorNPCCreateOutput.model_validate(
                wire_result.model_dump()
            )
            self.last_warnings = []
        return self._to_draft(result, creation_context, turn_index)

    def _to_draft(
        self,
        result: DirectorNPCCreateOutput,
        creation_context: NPCCreationContext,
        turn_index: int,
    ) -> DirectorNPCDraft:
        return DirectorNPCDraft(
            **result.identity.model_dump(),
            **result.behavior.model_dump(),
            **result.shell.model_dump(),
            source_setting_ids=list(creation_context.setting_ids),
            created_turn=turn_index,
            status="prepared",
            introduced_turn=None,
        )


def _existing_npc_view(npc) -> dict[str, str | None]:
    if isinstance(npc, dict):
        return {
            "name": npc.get("name"),
            "role": npc.get("role"),
            "core_motivation": npc.get("core_motivation"),
            "hidden_secret": npc.get("hidden_secret"),
        }
    return {
        "name": getattr(npc, "name", None),
        "role": getattr(npc, "role", None),
        "core_motivation": getattr(npc, "core_motivation", None),
        "hidden_secret": getattr(npc, "hidden_secret", None),
    }
