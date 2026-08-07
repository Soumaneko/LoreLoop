from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.turn_resolution import PlayerTurnResolution


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
