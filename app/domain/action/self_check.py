import re
from dataclasses import dataclass, field

from app.domain.action.schemas import ActionParseResult, Anchor, AtomicAction
from app.domain.current_state.schemas import CurrentState


@dataclass
class SelfCheckResult:
    original_action_count: int
    original_anchor_count: int
    final_action_count: int = 0
    final_anchor_count: int = 0
    fixes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


ALLOWED_ACTION_TYPES = {
    "move", "observe", "inspect_object", "interact_object",
    "speak", "signal_ally", "instruct_ally", "hide_or_stealth",
    "attack", "defend", "cast_or_use_power", "wait", "other",
}


class ActionSelfCheck:
    def __init__(self):
        self.last_result: SelfCheckResult | None = None

    def check(
        self,
        result: ActionParseResult,
        user_input: str,
        current_state: CurrentState,
    ) -> ActionParseResult:
        sc_result = SelfCheckResult(
            original_action_count=len(result.atomic_actions),
            original_anchor_count=len(result.anchors),
        )
        action_id_renames: dict[tuple[str, int], str] = {}

        actions = self._check_actions(
            result.atomic_actions,
            user_input,
            current_state,
            sc_result,
            action_id_renames,
        )
        anchors = self._check_anchors(result.anchors, actions, sc_result, action_id_renames)
        anchors = self._ensure_viewpoint_anchor(anchors, actions, current_state, sc_result)

        sc_result.final_action_count = len(actions)
        sc_result.final_anchor_count = len(anchors)
        self.last_result = sc_result

        return ActionParseResult(
            turn_intent=result.turn_intent,
            atomic_actions=actions,
            anchors=anchors,
            ambiguities=result.ambiguities,
        )

    def _check_actions(
        self,
        actions: list[AtomicAction],
        user_input: str,
        current_state: CurrentState,
        result: SelfCheckResult,
        action_id_renames: dict[tuple[str, int], str],
    ) -> list[AtomicAction]:
        if not actions:
            result.warnings.append("atomic_actions is empty")
            return actions

        seen_ids: set[str] = set()
        fixed_actions: list[AtomicAction] = []
        viewpoint = current_state.player.name or ""

        for i, action in enumerate(actions):
            updates: dict = {}

            if action.action_id in seen_ids:
                new_id = f"{action.action_id}_{i}"
                result.fixes.append(
                    f"action_id '{action.action_id}' duplicated, renamed to '{new_id}'"
                )
                updates["action_id"] = new_id
                action_id_renames[(action.action_id, i)] = new_id
            seen_ids.add(updates.get("action_id", action.action_id))

            expected_seq = len(fixed_actions) + 1
            if action.sequence != expected_seq:
                result.fixes.append(
                    f"action '{action.action_id}' sequence corrected: {action.sequence} → {expected_seq}"
                )
                updates["sequence"] = expected_seq

            if action.action_type not in ALLOWED_ACTION_TYPES:
                result.warnings.append(
                    f"action '{action.action_id}' has invalid type: {action.action_type}"
                )

            if not action.actor and viewpoint:
                updates["actor"] = viewpoint
                result.fixes.append(
                    f"action '{action.action_id}' actor filled from viewpoint: '{viewpoint}'"
                )

            normalized_input = user_input.replace(" ", "").replace("\n", "")
            normalized_raw = (action.raw_text or "").replace(" ", "").replace("\n", "")
            if normalized_raw and normalized_raw not in normalized_input:
                if not _fuzzy_substring(normalized_raw, normalized_input):
                    result.warnings.append(
                        f"action '{action.action_id}' raw_text is not a clear substring of user_input"
                    )

            if updates:
                fixed_actions.append(action.model_copy(update=updates))
            else:
                fixed_actions.append(action)

        return fixed_actions

    def _check_anchors(
        self,
        anchors: list[Anchor],
        actions: list[AtomicAction],
        result: SelfCheckResult,
        action_id_renames: dict[tuple[str, int], str],
    ) -> list[Anchor]:
        action_ids = {a.action_id for a in actions}
        renamed_by_old_id: dict[str, list[str]] = {}
        for (old_id, _index), new_id in action_id_renames.items():
            renamed_by_old_id.setdefault(old_id, []).append(new_id)

        seen_keys: set[tuple] = set()
        fixed_anchors: list[Anchor] = []

        for anchor in anchors:
            updates: dict = {}

            if not anchor.text or not anchor.text.strip():
                continue

            if not anchor.normalized or not anchor.normalized.strip():
                updates["normalized"] = anchor.text.strip()
                result.fixes.append(
                    f"anchor '{anchor.text}' normalized filled from text"
                )

            seen_ids: set[str] = set()
            valid_ids: list[str] = []
            for aid in anchor.action_ids:
                if aid in seen_ids:
                    result.fixes.append(
                        f"anchor '{anchor.text}' duplicate action_id '{aid}' removed"
                    )
                    continue

                matched = False

                if aid in action_ids:
                    valid_ids.append(aid)
                    seen_ids.add(aid)
                    matched = True

                if aid in renamed_by_old_id:
                    for new_id in renamed_by_old_id[aid]:
                        if new_id not in seen_ids:
                            valid_ids.append(new_id)
                            seen_ids.add(new_id)
                            result.fixes.append(
                                f"anchor '{anchor.text}' action_id '{aid}' updated to '{new_id}'"
                            )
                    matched = True

                if not matched:
                    result.fixes.append(
                        f"anchor '{anchor.text}' action_id '{aid}' removed (not found)"
                    )
            if valid_ids != anchor.action_ids:
                updates["action_ids"] = valid_ids

            dedup_key = (
                (updates.get("normalized") or anchor.normalized).strip(),
                anchor.kind,
            )
            if dedup_key in seen_keys:
                result.fixes.append(
                    f"anchor '{anchor.text}' duplicate removed (same normalized + kind)"
                )
                continue
            seen_keys.add(dedup_key)

            if updates:
                fixed_anchors.append(anchor.model_copy(update=updates))
            else:
                fixed_anchors.append(anchor)

        return fixed_anchors

    def _ensure_viewpoint_anchor(
        self,
        anchors: list[Anchor],
        actions: list[AtomicAction],
        current_state: CurrentState,
        result: SelfCheckResult,
    ) -> list[Anchor]:
        viewpoint = current_state.player.name or ""
        if not viewpoint:
            return anchors

        existing = {a.text for a in anchors}
        if viewpoint in existing:
            return anchors

        action_ids = [a.action_id for a in actions]
        new_anchor = Anchor(
            text=viewpoint,
            normalized=viewpoint,
            kind="character",
            source="implicit_from_scene",
            action_ids=action_ids,
            confidence=1.0,
        )
        result.fixes.append(
            f"viewpoint '{viewpoint}' added as anchor (implicit_from_scene)"
        )
        return anchors + [new_anchor]


def _fuzzy_substring(normalized_raw: str, normalized_input: str) -> bool:
    if len(normalized_raw) >= 4 and normalized_raw[:4] in normalized_input:
        return True
    if len(normalized_raw) >= 4 and normalized_raw[-4:] in normalized_input:
        return True
    tokens = re.findall(r"\w+", normalized_raw)
    if tokens:
        matches = sum(1 for t in tokens if t in normalized_input)
        return matches / len(tokens) >= 0.5
    return False
