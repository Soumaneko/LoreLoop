import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.domain.current_state import WorldClock
from app.domain.director import DirectorResourceRef
from app.domain.director_event import (
    DirectorActorRef,
    DirectorEventActorRole,
    DirectorEventSelectionResult,
    DirectorEventStore,
    EventHook,
    WorldImpact,
)


def _event_selection() -> DirectorEventSelectionResult:
    return DirectorEventSelectionResult(
        current_story_direction="港口失踪案仍在扩大。",
        reason="这是独立的长期世界活动。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            ),
            EventHook(
                hook_id="hook_rumor",
                kind="rumor",
                content="码头流传无灯货船靠岸的传闻。",
                trigger_anchors=["旧码头", "港务署"],
            ),
        ],
        world_impact=WorldImpact(
            description="猎户带走了码头封存的一批物资，外围变为凡人活动区。",
        ),
    )


def _event():
    return _event_selection().to_event(
        role="ambient",
        turn_index=3,
        current_clock=WorldClock(),
        actors=[DirectorActorRef(name="港务署")],
    )


def test_event_contract_has_generic_actor_refs_without_npc_dependency():
    event = _event()

    assert [actor.name for actor in event.actors] == ["港务署"]
    assert all(actor.ref is None for actor in event.actors)
    assert "director_npc_id" not in event.model_dump()
    assert "director_npc_id" not in DirectorEventSelectionResult.model_json_schema()[
        "properties"
    ]
    assert event.stage == "hidden"
    assert event.subject == "陷空山脚猎户"
    assert event.world_impact is not None


def test_event_store_binds_existing_resources_without_owning_their_lifecycle():
    from app.domain.director_event.schemas import DirectorActorRef

    store = DirectorEventStore()
    event = _event()
    store.save(event)

    updated = store.bind_actor(
        event.event_id,
        actor_name="林澈",
        actor_ref=DirectorResourceRef(kind="npc", resource_id="npc_1"),
        turn_index=4,
    )

    assert [actor.name for actor in updated.actors] == ["港务署", "林澈"]
    assert updated.actors[-1] == DirectorActorRef(
        name="林澈",
        ref=DirectorResourceRef(kind="npc", resource_id="npc_1"),
    )
    assert store.get_by_id(event.event_id) == updated
    store.close()


def test_bind_actor_upgrades_same_name_unreferenced_actor_instead_of_duplicating():
    from app.domain.director_event.schemas import DirectorActorRef, DirectorEventSelectionResult

    store = DirectorEventStore()
    event = _event_selection().to_event(
        role="ambient",
        turn_index=3,
        current_clock=WorldClock(),
        actors=[DirectorActorRef(name="港务署"), DirectorActorRef(name="林澈")],
    )
    store.save(event)

    updated = store.bind_actor(
        event.event_id,
        actor_name="林澈",
        actor_ref=DirectorResourceRef(kind="npc", resource_id="npc_1"),
        turn_index=4,
    )

    assert [actor.name for actor in updated.actors] == ["港务署", "林澈"]
    assert updated.actors[-1] == DirectorActorRef(
        name="林澈",
        ref=DirectorResourceRef(kind="npc", resource_id="npc_1"),
    )
    store.close()


def test_bind_actor_does_not_duplicate_same_resource_ref():
    from app.domain.director_event.schemas import DirectorActorRef

    store = DirectorEventStore()
    event = _event_selection().to_event(
        role="ambient",
        turn_index=3,
        current_clock=WorldClock(),
        actors=[DirectorActorRef(name="港务署")],
    )
    store.save(event)
    ref = DirectorResourceRef(kind="npc", resource_id="npc_1")

    store.bind_actor(
        event.event_id,
        actor_name="林澈",
        actor_ref=ref,
        turn_index=4,
    )
    updated = store.bind_actor(
        event.event_id,
        actor_name="林澈",
        actor_ref=ref,
        turn_index=5,
    )

    assert [actor.name for actor in updated.actors] == ["港务署", "林澈"]
    assert sum(actor.name == "林澈" for actor in updated.actors) == 1
    store.close()


def test_event_store_rejects_previous_schema_instead_of_migrating_it(tmp_path):
    db_path = tmp_path / "director_events.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE schema_versions(component TEXT PRIMARY KEY, version INTEGER)"
    )
    conn.execute(
        "INSERT INTO schema_versions(component, version) VALUES ('director_event', 8)"
    )
    conn.commit()
    conn.close()

    try:
        DirectorEventStore(str(db_path))
    except RuntimeError as exc:
        assert "expected 11" in str(exc)
    else:
        raise AssertionError("previous DirectorEvent schema must be rejected")


def test_event_domain_does_not_import_director_npc():
    from pathlib import Path

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app/domain/director_event").glob("*.py")
    )
    assert "director_npc" not in source


def test_hooks_are_stored_but_hidden_from_consumer_projections():
    from app.domain.director_event.views import project_director_event

    event = _event()
    assert event.hook_pool
    assert not event.hook_pool[0].consumed

    stored = event.model_dump()
    assert stored["hook_pool"]

    projected = project_director_event(event)
    assert "hook_sound" in projected
    assert "intent" not in projected
    assert "world_impact" not in projected


def test_project_director_events_forces_engaged_view():
    from app.domain.director_event.views import project_director_events

    event = _event().model_copy(update={"stage": "hidden"})

    projected = json.loads(
        project_director_events([event], effective_stage="engaged")
    )[0]

    assert projected["stage"] == "engaged"
    assert projected["macro_intent"]
    assert projected["subject"]
    assert projected["actors"]
    assert "hooks" not in projected


def test_multiple_active_events_allowed_without_role_limit():
    store = DirectorEventStore()
    first = _event_selection().to_event(
        role="arc", turn_index=1, current_clock=WorldClock()
    )
    second = _event_selection().to_event(
        role="arc", turn_index=2, current_clock=WorldClock()
    )
    store.save(first)
    store.save(second)

    assert first.stage == "hidden"
    assert second.stage == "hidden"
    assert len(store.list_active()) == 2
    store.close()


def test_stage_transitions_and_hook_consumption():
    store = DirectorEventStore()
    event = _event()
    store.save(event)

    assert store.set_stage(event.event_id, "engaged", turn_index=5)
    engaged = store.get_by_id(event.event_id)
    assert engaged is not None and engaged.stage == "engaged"

    assert store.mark_hooks_consumed(
        event.event_id,
        ["hook_sound"],
        turn_index=5,
    )
    consumed = store.get_by_id(event.event_id)
    assert consumed is not None
    assert {hook.hook_id for hook in consumed.hook_pool if hook.consumed} == {
        "hook_sound"
    }
    assert {hook.hook_id for hook in consumed.hook_pool if not hook.consumed} == {
        "hook_rumor"
    }

    assert store.resolve_event(
        event.event_id,
        turn_index=6,
        resolved_reason="事件核心冲突收束。",
    )
    resolved = store.get_by_id(event.event_id)
    assert resolved is not None and resolved.stage == "resolved"
    assert resolved.resolved_turn == 6
    store.close()


def test_world_impact_is_stored_but_not_applied_to_setting():
    from app.domain.world_setting.store import WorldSettingStore

    store = DirectorEventStore()
    event = _event()
    store.save(event)
    store.resolve_event(
        event.event_id,
        turn_index=6,
        resolved_reason="猎户离开码头。",
    )

    setting_store = WorldSettingStore()
    assert setting_store.count() == 0
    setting_store.close()
    store.close()


def test_consuming_a_new_hook_does_not_reopen_previous_hook():
    store = DirectorEventStore()
    event = _event()
    store.save(event)

    assert store.mark_hooks_consumed(
        event.event_id,
        ["hook_sound"],
        turn_index=5,
    )
    assert store.mark_hooks_consumed(
        event.event_id,
        ["hook_rumor"],
        turn_index=6,
    )
    persisted = store.get_by_id(event.event_id)
    assert persisted is not None
    assert {hook.hook_id for hook in persisted.hook_pool if hook.consumed} == {
        "hook_sound",
        "hook_rumor",
    }
    assert not any(not hook.consumed for hook in persisted.hook_pool)
    store.close()


def test_reconsuming_a_hook_preserves_original_consumed_turn():
    store = DirectorEventStore()
    event = _event()
    store.save(event)

    assert store.mark_hooks_consumed(
        event.event_id,
        ["hook_sound"],
        turn_index=5,
    )
    assert store.mark_hooks_consumed(
        event.event_id,
        ["hook_sound"],
        turn_index=9,
    )
    persisted = store.get_by_id(event.event_id)
    assert persisted is not None
    hook_sound = next(
        hook for hook in persisted.hook_pool if hook.hook_id == "hook_sound"
    )
    assert hook_sound.consumed is True
    assert hook_sound.consumed_turn == 5
    store.close()


def test_selection_actor_roles_require_unique_keys_and_event_uses_bound_actors():
    from pydantic import ValidationError

    selection = DirectorEventSelectionResult(
        current_story_direction="港口失踪案仍在扩大。",
        reason="这是独立的长期世界活动。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(
            description="猎户带走了码头封存的一批物资，外围变为凡人活动区。",
        ),
    )

    event = selection.to_event(
        role="ambient",
        turn_index=3,
        current_clock=WorldClock(),
        actors=[DirectorActorRef(name="林澈"), DirectorActorRef(name="港务署")],
    )
    assert [actor.name for actor in event.actors] == ["林澈", "港务署"]

    with pytest.raises(ValidationError):
        DirectorEventSelectionResult(
            current_story_direction="港口失踪案仍在扩大。",
            reason="这是独立的长期世界活动。",
            title="无灯船",
            duration_phases=7,
            actor_roles=[
                DirectorEventActorRole(actor_key="actor_1", role_in_event="调查"),
                DirectorEventActorRole(actor_key="actor_1", role_in_event="掩护"),
            ],
            subject="陷空山脚猎户",
            intent="查明灵气异动",
            method="组队搜山",
            macro_intent="猎户为了生存不择手段。",
            hook_pool=[
                EventHook(
                    hook_id="hook_sound",
                    kind="auditory",
                    content="深夜水面传来无人摇橹声。",
                    trigger_anchors=["旧码头"],
                )
            ],
            world_impact=WorldImpact(
                description="猎户带走了码头封存的一批物资，外围变为凡人活动区。",
            ),
        )


def test_selection_rejects_duration_beyond_world_clock_limit():
    payload = _event_selection().model_dump(mode="json")
    payload["duration_phases"] = 141

    with pytest.raises(ValidationError):
        DirectorEventSelectionResult.model_validate(payload)


def test_event_deadline_is_derived_from_required_current_clock():
    event = _event_selection().to_event(
        role="ambient",
        turn_index=3,
        current_clock=WorldClock(day=4, phase="黄昏"),
    )

    assert event.deadline_clock == WorldClock(day=5, phase="黄昏")


def test_actor_role_rejects_blank_role_in_event():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DirectorEventActorRole(
            actor_key="actor_1",
            role_in_event="   ",
        )
