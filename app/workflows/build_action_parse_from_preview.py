from app.domain.action.schemas import ActionParseResult, Anchor, AtomicAction
from app.domain.action.self_check import ActionSelfCheck
from app.domain.action_preview.schemas import ActionPreview
from app.domain.current_state.schemas import CurrentState


def build_parse_from_preview(
    preview: ActionPreview,
    current_state: CurrentState,
    *,
    self_check: ActionSelfCheck | None = None,
) -> ActionParseResult:
    action = AtomicAction(
        action_id="A1",
        sequence=1,
        actor=current_state.player.name,
        action_type=preview.action_type,
        raw_text=preview.label,
        verb=preview.verb,
        target=preview.target,
        method=preview.method,
        goal=preview.goal,
        condition=None,
        likely_needs_roll=preview.likely_needs_roll,
        roll_hint=None,
        risk_level=preview.risk_level,
        confidence=1.0,
    )
    anchors = [
        Anchor(
            text=anchor.text,
            normalized=anchor.normalized or anchor.text,
            kind=anchor.kind,
            source="explicit",
            action_ids=["A1"],
            confidence=anchor.confidence,
        )
        for anchor in preview.anchors
    ]
    result = ActionParseResult(
        turn_intent=preview.label,
        atomic_actions=[action],
        anchors=anchors,
        ambiguities=[],
    )

    checker = self_check or ActionSelfCheck()
    return checker.check(result, preview.label, current_state)
