import pytest

from app.domain.current_state import WorldClock
from app.graph import confirmation_nodes
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import seed_deadline_event
from app.tests.factories.director import make_review_node_state


def test_hidden_event_uses_original_world_impact_when_world_clock_expires(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event_id = seed_deadline_event(stores)
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": "玩家没有接触事件。",
                "action": "keep_hidden",
                "consume_hook_ids": [],
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
    assert resolved.final_world_impact == resolved.world_impact


def test_hidden_event_expires_without_impact_when_player_engages_at_deadline(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event_id = seed_deadline_event(stores)
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": "玩家正在调查事件主体。",
                "action": "engage",
                "consume_hook_ids": ["hook_rumor"],
                "player_still_involved": True,
                "final_world_impact": None,
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
    assert resolved.final_world_impact is None


def test_due_engaged_event_exits_without_impact_while_player_is_involved(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event_id = seed_deadline_event(stores, stage="engaged")
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": "玩家仍在追查沉船账本。",
                "action": "engage",
                "resolved_reason": None,
                "player_still_involved": True,
                "final_world_impact": None,
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
    assert resolved.final_world_impact is None


def test_due_engaged_event_applies_adjusted_impact_after_player_disengages(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event_id = seed_deadline_event(stores, stage="engaged")
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": event_id,
                "reason": "玩家已经转向其他目标，但此前破坏了一条货路。",
                "action": "engage",
                "resolved_reason": None,
                "player_still_involved": False,
                "final_world_impact": {
                    "description": "码头垄断仍然形成，但失去一条主要货路。",
                },
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
    assert resolved.final_world_impact is not None
    assert resolved.final_world_impact.description == (
        "码头垄断仍然形成，但失去一条主要货路。"
    )
