import json

import pytest

from app.domain.core_memory import CampaignSetupProposal, InitConfig
from app.runtime.initialization import InitializationStore, commit_campaign_setup
from app.runtime.initialization_draft import (
    InitializationDraft,
    clear_initialization_draft,
    load_initialization_draft,
    save_initialization_draft,
)
from app.runtime.session import get_campaign_runtime


def _draft():
    return {
        "version": 4,
        "stage": "campaign_setup",
        "form": {
            "fields": {"world_name": "艾兰"},
        },
        "config": {"version": 4, "world_name": "艾兰"},
        "campaign_setup": {
            "version": 2,
            "currency_unit": "银币",
            "public_power_baseline": {
                "source": "魔法",
                "prevalence": "少数人掌握",
                "learning_difficulty": "困难",
                "limitation": "不能复活死者。",
            },
            "peoples_and_beings": ["人类", "精灵"],
            "major_world_conflicts": ["帝国扩张持续挤压边境诸国。"],
            "dominant_powers": ["北方帝国", "圣辉教会"],
            "civilization_baseline": "以马车、冷兵器和手工业为主。",
        },
    }


def test_initialization_draft_round_trip_and_clear(campaign_runtime):
    saved = save_initialization_draft(_draft())

    assert saved == InitializationDraft.model_validate(_draft()).model_dump(
        mode="json"
    )
    assert load_initialization_draft() == saved

    clear_initialization_draft()

    assert load_initialization_draft() is None


def test_corrupted_initialization_json_is_not_treated_as_missing(tmp_path):
    path = tmp_path / "initialization.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        InitializationStore(path).load()


def test_initialization_draft_rejects_unknown_contract_fields():
    with pytest.raises(ValueError):
        save_initialization_draft({**_draft(), "unexpected_stage": "old"})


def test_unreadable_config_draft_is_preserved(campaign_runtime):
    runtime = get_campaign_runtime()
    stale = {**_draft(), "config": {"version": 3, "world_name": "艾兰"}}
    path = runtime.paths.initialization_draft_json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")

    original = path.read_bytes()
    with pytest.raises(ValueError):
        load_initialization_draft()
    assert path.read_bytes() == original


def test_unreadable_draft_envelope_is_preserved(campaign_runtime):
    runtime = get_campaign_runtime()
    old = {
        "version": 3,
        "stage": "campaign_setup",
        "form": {"world_name": "艾兰"},
        "config": {
            "version": 3,
            "director_enabled": True,
            "world_name": "艾兰",
        },
        "campaign_setup": None,
    }
    path = runtime.paths.initialization_draft_json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    original = path.read_bytes()
    with pytest.raises(ValueError):
        load_initialization_draft()
    assert path.read_bytes() == original


def test_stale_config_version_draft_is_rejected_on_save():
    stale = {**_draft(), "config": {"version": 3, "world_name": "艾兰"}}
    with pytest.raises(ValueError, match="version"):
        save_initialization_draft(stale)


def test_initialization_draft_cannot_be_written_after_initialization(campaign_runtime):
    commit_campaign_setup(
        InitConfig(
            world_name="艾兰",
            world_style="西幻",
            ability_system="魔法",
            protagonist_name="卢西安",
            story_expectation="探索世界。",
            failure_severity="适中",
        ),
        CampaignSetupProposal(
            currency_unit="银币",
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
        ),
    )

    with pytest.raises(RuntimeError, match="already committed"):
        save_initialization_draft(_draft())


def test_committed_core_memory_must_match_config_and_proposal_projection(campaign_runtime):
    config = InitConfig(
        world_name="艾兰",
        world_style="西幻",
        ability_system="魔法",
        protagonist_name="卢西安",
        story_expectation="探索世界。",
        failure_severity="适中",
    )
    proposal = CampaignSetupProposal(
        currency_unit="银币",
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
    record = commit_campaign_setup(config, proposal)
    corrupted_item = record.core_memory.items[0].model_copy(
        update={"content": "与配置不一致的世界"}
    )
    corrupted_core = record.core_memory.model_copy(update={
        "items": [corrupted_item, *record.core_memory.items[1:]],
    })
    runtime = get_campaign_runtime()
    runtime.initialization.save(record.model_copy(update={
        "core_memory": corrupted_core,
    }))
    runtime.stores.core_memory.save(corrupted_core)

    with pytest.raises(
        RuntimeError,
        match="does not match its config and campaign setup",
    ):
        commit_campaign_setup(config, proposal)


def test_incomplete_draft_round_trips_without_completeness_checks(campaign_runtime):
    draft = {
        "version": 4,
        "stage": "campaign_setup",
        "form": {
            "fields": {
                "world_name": "艾兰",
                "setup_currency_unit": "",
                "setup_power_limitation": "",
                "setup_peoples_and_beings": "人类\n\n精灵",
            },
            "protagonist_abilities": [
                {"name": "", "description": "只填了说明"},
            ],
        },
        "config": {"version": 4, "world_name": "艾兰"},
        "campaign_setup": {
            "version": 2,
            "currency_unit": "",
            "public_power_baseline": {
                "source": " 武学 ",
                "prevalence": "少数人掌握",
                "learning_difficulty": "适中",
                "limitation": "",
            },
            "peoples_and_beings": ["人类", "", "精灵"],
            "major_world_conflicts": [""],
            "dominant_powers": [""],
            "civilization_baseline": "",
        },
    }

    saved = save_initialization_draft(draft)

    assert load_initialization_draft() == saved
    assert saved["form"]["fields"]["setup_power_limitation"] == ""
    assert saved["form"]["fields"]["setup_peoples_and_beings"] == "人类\n\n精灵"
    assert saved["form"]["protagonist_abilities"] == [
        {"name": "", "description": "只填了说明"},
    ]
    assert saved["campaign_setup"]["peoples_and_beings"] == ["人类", "", "精灵"]
    assert saved["campaign_setup"]["public_power_baseline"]["source"] == " 武学 "
