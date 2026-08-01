import sqlite3

from app.domain.current_state import WorldClock
from app.domain.director import DirectorResourceRef
from app.domain.director_event import (
    DirectorEventEntryPoint,
    DirectorEventSelectionResult,
    DirectorEventStore,
)


def _event():
    return DirectorEventSelectionResult(
        current_story_direction="港口失踪案仍在扩大。",
        reason="这是独立的长期世界活动。",
        title="无灯船",
        actors=["港务署"],
        objective="查明无灯船的来源。",
        bridge_anchors=["旧码头"],
        milestones=["船影出现", "货物失踪", "航道封锁"],
        milestone_interval_days=[1.0, 2.0],
        premise="没有灯火的货船在夜间靠岸。",
        entry_points=[
            DirectorEventEntryPoint(
                trigger_anchors=["旧码头"],
                visible_sign="水面出现无灯船影。",
                immediate_effect="夜间航道临时关闭。",
            )
        ],
    ).to_event(
        role="ambient",
        turn_index=3,
        current_clock=WorldClock(day=2, phase="晚上"),
    )


def test_event_contract_has_generic_actor_refs_without_npc_dependency():
    event = _event()

    assert event.actor_refs == []
    assert "director_npc_id" not in event.model_dump()
    assert "director_npc_id" not in DirectorEventSelectionResult.model_json_schema()[
        "properties"
    ]


def test_event_store_binds_existing_resources_without_owning_their_lifecycle():
    store = DirectorEventStore()
    event = _event()
    store.save(event)

    updated = store.bind_actor(
        event.event_id,
        actor_name="林澈",
        actor_ref=DirectorResourceRef(kind="npc", resource_id="npc_1"),
        turn_index=4,
    )

    assert updated.actors == ["港务署", "林澈"]
    assert updated.actor_refs == [
        DirectorResourceRef(kind="npc", resource_id="npc_1")
    ]
    assert store.get_by_id(event.event_id) == updated
    store.close()


def test_event_store_rejects_previous_schema_instead_of_migrating_it(tmp_path):
    db_path = tmp_path / "director_events.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE schema_versions(component TEXT PRIMARY KEY, version INTEGER)"
    )
    conn.execute(
        "INSERT INTO schema_versions(component, version) VALUES ('director_event', 5)"
    )
    conn.commit()
    conn.close()

    try:
        DirectorEventStore(str(db_path))
    except RuntimeError as exc:
        assert "expected 6" in str(exc)
    else:
        raise AssertionError("previous DirectorEvent schema must be rejected")


def test_event_domain_does_not_import_director_npc():
    from pathlib import Path

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app/domain/director_event").glob("*.py")
    )
    assert "director_npc" not in source


def test_entry_points_are_stored_but_hidden_from_consumer_projections():
    from app.domain.director_event.selector import _event_list_view
    from app.domain.director_event.views import project_director_event

    event = _event()
    assert event.entry_points

    stored = event.model_dump()
    assert stored["entry_points"]

    assert "entry_points" not in event.semantic_view()
    assert "entry_points" not in project_director_event(event)
    assert "entry_points" not in _event_list_view(event)
