from app.domain.core_memory import CoreMemory, InitConfig, build_initial_current_state
from app.domain.scene_writer.schemas import SceneWriterResult
from app.graph.opening_rewrite import rewrite_opening_stream
from app.runtime.player_state import load_player_state
from app.runtime.session import PendingOpening, get_campaign_runtime


def _pending_opening(*, turn_id: str, group_id: str, version_index: int):
    config = InitConfig(
        world_name="艾兰",
        world_style="西幻",
        protagonist_name="卢西安",
    )
    return PendingOpening(
        turn_id=turn_id,
        group_id=group_id,
        version_index=version_index,
        config=config,
        current_state=build_initial_current_state(config),
        scene_result=SceneWriterResult(scene_text=f"开场版本 {version_index}"),
    )


def test_opening_rewrite_adds_version_without_mutating_source(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()
    source = _pending_opening(
        turn_id="opening-1",
        group_id="opening-group",
        version_index=1,
    )
    runtime.pending_openings.add(source)
    runtime.stores.core_memory().save(CoreMemory(items=[]))
    calls = []

    class Gateway:
        def set_trace_context(self, **_kwargs):
            pass

        def run(self, task, input_data, output_schema, **_kwargs):
            calls.append((task, input_data))
            return output_schema.model_validate({"scene_text": "全新开场"})

    configure_runtime_services(gateway_factory=Gateway)

    events = list(rewrite_opening_stream(source.turn_id))
    variants = runtime.pending_openings.variants(source.group_id)

    assert [event["step"] for event in events] == [
        "opening_rewrite_started",
        "scene_written",
    ]
    assert [item.version_index for item in variants] == [1, 2]
    assert variants[0].scene_result.scene_text == "开场版本 1"
    assert variants[1].scene_result.scene_text == "全新开场"
    assert variants[1].turn_id != source.turn_id
    assert [task for task, _payload in calls] == ["opening_scene"]


def test_opening_rewrite_failure_preserves_only_existing_version(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()
    source = _pending_opening(
        turn_id="opening-1",
        group_id="opening-group",
        version_index=1,
    )
    runtime.pending_openings.add(source)
    runtime.stores.core_memory().save(CoreMemory(items=[]))

    class Gateway:
        def set_trace_context(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            raise RuntimeError("opening failed")

    configure_runtime_services(gateway_factory=Gateway)

    try:
        list(rewrite_opening_stream(source.turn_id))
    except RuntimeError as exc:
        assert str(exc) == "opening failed"
    else:
        raise AssertionError("rewrite must propagate generation failure")

    assert runtime.pending_openings.variants(source.group_id) == [source]


def test_player_state_projects_opening_versions_as_one_group():
    runtime = get_campaign_runtime()
    runtime.stores.core_memory().save(CoreMemory(items=[]))
    runtime.pending_openings.add(_pending_opening(
        turn_id="opening-1",
        group_id="opening-group",
        version_index=1,
    ))
    runtime.pending_openings.add(_pending_opening(
        turn_id="opening-2",
        group_id="opening-group",
        version_index=2,
    ))

    payload = load_player_state()

    assert len(payload["pending"]) == 1
    assert payload["pending"][0]["kind"] == "opening"
    assert payload["pending"][0]["group_id"] == "opening-group"
    assert [
        item["version_index"]
        for item in payload["pending"][0]["variants"]
    ] == [1, 2]
