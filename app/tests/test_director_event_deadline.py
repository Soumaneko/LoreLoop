import pytest

from app.domain.current_state import WorldClock
from app.graph import confirmation_nodes
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import seed_deadline_event
from app.tests.factories.director import make_review_node_state


@pytest.mark.parametrize(
    "review_extra, expect_original",
    [
        (
            {"action": "keep_hidden", "consume_hook_ids": [], "reason": "玩家没有接触事件。"},
            True,
        ),
        (
            {
                "action": "engage",
                "consume_hook_ids": ["hook_rumor"],
                "player_still_involved": True,
                "final_world_impact": None,
                "reason": "玩家正在调查事件主体。",
            },
            False,
        ),
    ],
)
def test_hidden_event_deadline_resolves(tmp_path, review_extra, expect_original):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event_id = seed_deadline_event(stores)
    review = {"event_id": event_id, **review_extra}
    # keep_hidden case needs no player_still_involved; engage case already has it
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={"reviews": [review]},
    )

    confirmation_nodes.director_event_review_node(
        make_review_node_state(
            stores,
            gateway,
            world_clock=WorldClock(day=2, phase="清晨"),
        )
    )

    with stores.director_event as store:
        resolved = store.get_by_id(event_id)
    assert resolved is not None
    assert resolved.stage == "resolved"
    if expect_original:
        assert resolved.final_world_impact == resolved.world_impact
    else:
        assert resolved.final_world_impact is None


@pytest.mark.parametrize(
    "player_still_involved, final_world_impact, reason",
    [
        (True, None, "玩家仍在追查沉船账本。"),
        (
            False,
            {"description": "码头垄断仍然形成，但失去一条主要货路。"},
            "玩家已经转向其他目标，但此前破坏了一条货路。",
        ),
    ],
)
def test_engaged_event_deadline_resolves(tmp_path, player_still_involved, final_world_impact, reason):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event_id = seed_deadline_event(stores, stage="engaged")
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": reason,
                "action": "engage",
                "resolved_reason": None,
                "player_still_involved": player_still_involved,
                "final_world_impact": final_world_impact,
            }],
        },
    )

    confirmation_nodes.director_event_review_node(
        make_review_node_state(
            stores,
            gateway,
            world_clock=WorldClock(day=2, phase="清晨"),
        )
    )

    with stores.director_event as store:
        resolved = store.get_by_id(event_id)
    assert resolved is not None
    assert resolved.stage == "resolved"
    if final_world_impact is None:
        assert resolved.final_world_impact is None
    else:
        assert resolved.final_world_impact is not None
        assert resolved.final_world_impact.description == final_world_impact["description"]
