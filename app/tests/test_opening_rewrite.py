from app.domain.core_memory import CampaignSetupProposal, InitConfig
from app.domain.core_memory.init import build_initial_current_state
from app.domain.opening_foundation import OpeningFoundation
from app.domain.scene_writer.schemas import SceneWriterResult
from app.graph.opening_rewrite import rewrite_opening_stream
from app.runtime.initialization import commit_campaign_setup
from app.runtime.player_state import load_player_state
from app.runtime.session import PendingOpening, get_campaign_runtime


def _config():
    return InitConfig(
        world_name="艾兰",
        world_style="西幻",
        ability_system="魔法",
        protagonist_name="卢西安",
        story_expectation="探索、成长并建立关系。",
        failure_severity="适中",
    )


def _proposal():
    return CampaignSetupProposal(
        currency_unit="金币",
        public_power_baseline={
            "source": "魔法",
            "prevalence": "少数人掌握",
            "learning_difficulty": "困难",
            "limitation": "不能复活死者。",
        },
        peoples_and_beings=["人类", "精灵"],
        major_world_conflicts=["帝国扩张持续挤压边境诸国。"],
        dominant_powers=["北方帝国", "圣辉教会"],
        civilization_baseline="以马车、冷兵器和手工业为主。",
    )


def _foundation():
    return OpeningFoundation.model_validate({
        "starting_location": {
            "name": "白塔港",
            "description": "一座贸易港。",
        },
        "protagonist_opening_situation": (
            "卢西安刚刚抵达，登记大厅突然封门。"
        ),
        "initial_resources": {
            "inventory": [],
            "money": 5,
            "fixed_assets": [],
        },
    })


def _prepare_record():
    runtime = get_campaign_runtime()
    record = commit_campaign_setup(_config(), _proposal())
    record = record.model_copy(update={
        "phase": "opening_pending",
        "foundation": _foundation(),
    })
    runtime.initialization.save(record)
    return record


def _pending_opening(
    *,
    turn_id: str,
    group_id: str,
    version_index: int,
    fingerprint: str,
):
    config = _config()
    return PendingOpening(
        turn_id=turn_id,
        group_id=group_id,
        version_index=version_index,
        initialization_fingerprint=fingerprint,
        config=config,
        experience_mode="creator",
        current_state=build_initial_current_state(
            config,
            location="白塔港",
            currency_unit="金币",
        ),
        scene_result=SceneWriterResult(scene_text=f"开场版本 {version_index}"),
    )


def test_opening_rewrite_adds_version_and_reuses_foundation(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()
    record = _prepare_record()
    source = _pending_opening(
        turn_id="opening-1",
        group_id="opening-group",
        version_index=1,
        fingerprint=record.fingerprint,
    )
    runtime.pending_openings.add(source)
    calls = []

    class Gateway:
        def set_trace_context(self, **_kwargs):
            pass

        def run(self, task, input_data, output_schema, **_kwargs):
            calls.append((task, input_data))
            return output_schema.model_validate({"scene_text": "全新开场"})

    configure_runtime_services(gateway_factory=lambda **_kwargs: Gateway())
    events = list(rewrite_opening_stream(source.turn_id))
    variants = runtime.pending_openings.variants(source.group_id)

    assert [event["step"] for event in events] == [
        "opening_rewrite_started",
        "scene_written",
    ]
    assert [item.version_index for item in variants] == [1, 2]
    assert variants[0].scene_result.scene_text == "开场版本 1"
    assert variants[1].scene_result.scene_text == "全新开场"
    assert variants[1].initialization_fingerprint == record.fingerprint
    assert [task for task, _payload in calls] == ["opening_scene"]
    assert "opening_foundation_json" in calls[0][1]


def test_opening_rewrite_failure_preserves_existing_version(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()
    record = _prepare_record()
    source = _pending_opening(
        turn_id="opening-1",
        group_id="opening-group",
        version_index=1,
        fingerprint=record.fingerprint,
    )
    runtime.pending_openings.add(source)

    class Gateway:
        def set_trace_context(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            raise RuntimeError("opening failed")

    configure_runtime_services(gateway_factory=lambda **_kwargs: Gateway())

    try:
        list(rewrite_opening_stream(source.turn_id))
    except RuntimeError as exc:
        assert str(exc) == "opening failed"
    else:
        raise AssertionError("rewrite must propagate generation failure")

    assert runtime.pending_openings.variants(source.group_id) == [source]


def test_player_state_projects_opening_versions_without_private_foundation():
    runtime = get_campaign_runtime()
    record = _prepare_record()
    for version_index in (1, 2):
        runtime.pending_openings.add(_pending_opening(
            turn_id=f"opening-{version_index}",
            group_id="opening-group",
            version_index=version_index,
            fingerprint=record.fingerprint,
        ))

    payload = load_player_state()

    assert len(payload["pending"]) == 1
    assert [
        item["version_index"]
        for item in payload["pending"][0]["variants"]
    ] == [1, 2]
    assert payload["initialization"]["campaign_setup"] is not None
    assert "foundation" not in payload["initialization"]
