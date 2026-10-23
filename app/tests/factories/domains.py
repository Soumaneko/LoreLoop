from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.action_judgment import PlayerTurnResolution


def make_action_preview() -> ActionPreviewResult:
    return ActionPreviewResult(previews=[
        {"level": "追击施压", "label": "追上去", "good_result": "追上目标。", "risk": "可能暴露。"},
        {"level": "静待时机", "label": "留在原地", "good_result": "保持安全。", "risk": "可能错失机会。"},
        {"level": "痕迹追查", "label": "检查痕迹", "good_result": "发现线索。", "risk": "会消耗时间。"},
        {"level": "声东击西", "label": "制造声响", "good_result": "引开注意。", "risk": "可能惊动更多人。"},
    ])


def make_player_resolution() -> PlayerTurnResolution:
    return PlayerTurnResolution(actions=[{
        "action_text": "追问木偶师",
        "resolution_source": "roll",
        "outcome_band": "partial_success",
        "dice": "2d20kh",
        "raw_values": [7, 11],
        "modifiers": [{
            "label": "对方戒备",
            "reason": "木偶师对直接追问有所防备。",
            "value": -1,
        }],
        "modifier_total": -1,
        "final_value": 10,
        "dice_sides": 20,
    }])


def make_pending_turn(**changes):
    from app.domain.core_memory import CoreMemory
    from app.domain.current_state import make_default_state
    from app.domain.narrative_composer.schemas import NarrativeComposerContext
    from app.domain.plot_outline.schemas import PlotOutlineResult
    from app.domain.scene_writer.schemas import SceneWriterResult
    from app.runtime.session import PendingTurn

    state = changes.get("state_before", make_default_state())
    user_input = changes.get("user_input", "观察门外")
    values = dict(
        turn_id="turn", group_id="group", version_index=1,
        user_input=user_input, difficulty="普通", max_chars=1500,
        experience_mode="creator", state_before=state,
        context=NarrativeComposerContext(
            user_input=user_input, current_state=state, core_memory=CoreMemory(items=[]),
        ),
        outline=PlotOutlineResult(scene_stopping_point="门外的动静已经清楚。"),
        scene_result=SceneWriterResult(scene_text="门外传来脚步声。"),
    )
    values.update(changes)
    return PendingTurn(**values)
