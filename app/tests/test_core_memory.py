import json

import pytest
from pydantic import ValidationError

from app.core.llm import MockLLM
from app.domain.core_memory import (
    CampaignSetupAgent,
    CampaignSetupProposal,
    InitConfig,
    build_core_memory,
    build_initial_current_state,
)
from app.domain.current_state import InventoryItem
from app.graph.initialization_helpers import preview_initialization_config


def make_config(**updates) -> InitConfig:
    values = {
        "world_name": "艾兰",
        "world_style": "西幻",
        "ability_system": "魔法",
        "protagonist_name": "卢西安",
        "story_expectation": "逐步成长、探索陌生世界并建立真实关系。",
        "failure_severity": "适中",
    }
    values.update(updates)
    return InitConfig(**values)


def make_proposal() -> CampaignSetupProposal:
    return CampaignSetupProposal(
        currency_unit="银币",
        public_power_baseline={
            "source": "魔法",
            "prevalence": "少数人掌握",
            "learning_difficulty": "困难",
            "limitation": "不能复活死者。",
        },
    )


def test_init_config_contains_only_player_layer_and_keeps_dropdown_contracts():
    config = make_config()
    assert set(config.model_dump()) == {
        "version",
        "director_enabled",
        "world_name",
        "ability_system",
        "custom_ability_system",
        "world_style",
        "protagonist_name",
        "protagonist_gender",
        "protagonist_age",
        "protagonist_introduction",
        "protagonist_abilities",
        "story_expectation",
        "failure_severity",
    }
    with pytest.raises(ValidationError):
        make_config(ability_system="随意手写的体系")
    with pytest.raises(ValidationError):
        make_config(ability_system="自定义")
    custom = make_config(
        ability_system="自定义",
        custom_ability_system="以梦境交换记忆",
    )
    assert custom.custom_ability_system == "以梦境交换记忆"


def test_player_layer_and_public_proposal_project_to_core_memory():
    memory = build_core_memory(
        make_config(
            protagonist_abilities=[
                {"name": "闪现", "description": "短距离移动，但会迅速疲惫"}
            ]
        ),
        make_proposal(),
    )
    by_id = {item.id: item for item in memory.items}
    assert set(by_id) == {
        "core_world_identity",
        "core_player_identity",
        "core_story_expectation",
        "core_failure_severity",
        "core_player_abilities",
        "core_currency_system",
        "core_public_power_baseline",
    }
    assert by_id["core_story_expectation"].source == "player"
    assert by_id["core_currency_system"].source == "campaign_setup"
    assert "少数人掌握" in by_id["core_public_power_baseline"].content
    assert "不能复活死者" in by_id["core_public_power_baseline"].content


def test_campaign_setup_agent_generates_only_public_world_conventions():
    config = make_config()
    mock = MockLLM()
    mock.set_responses([json.dumps({
        "version": 1,
        "currency_unit": "银币",
        "public_power_baseline": {
            "source": "魔法",
            "prevalence": "少数人掌握",
            "learning_difficulty": "困难",
            "limitation": "不能复活死者。",
        },
    }, ensure_ascii=False)])

    result = CampaignSetupAgent(mock.build_gateway()).generate(
        config=config,
    )
    assert result == make_proposal().model_copy(update={
        "public_power_baseline": result.public_power_baseline,
    })
    assert set(result.model_dump()) == {
        "version",
        "currency_unit",
        "public_power_baseline",
    }


@pytest.mark.parametrize(
    "limitation",
    [
        ["不能复活死者。"],
        "不能复活死者。\n也不能跨越时间。",
        "不能复活死者。也不能跨越时间。",
        "不能复活死者；也不能跨越时间。",
        "这是一条超过长度限制的能力边界" * 10,
    ],
)
def test_public_power_baseline_accepts_only_one_short_sentence(limitation):
    payload = make_proposal().model_dump(mode="json")
    payload["public_power_baseline"]["limitation"] = limitation
    with pytest.raises(ValidationError):
        CampaignSetupProposal.model_validate(payload)


def test_foundation_resources_project_to_initial_current_state():
    state = build_initial_current_state(
        make_config(protagonist_age=22),
        location="白塔港",
        currency_unit="银币",
        inventory=[InventoryItem(name="旅行包", quantity=1)],
        money=12,
        fixed_assets=["城外小屋"],
    )
    assert state.scene.location == "白塔港"
    assert state.scene.participant_names == ["卢西安"]
    assert state.resources.inventory[0].name == "旅行包"
    assert state.resources.money == 12
    assert state.resources.money_unit == "银币"
    assert state.resources.fixed_assets == ["城外小屋"]
    assert state.world_clock.label == "第1日清晨"


def test_preview_returns_complete_player_core_memory_without_writing():
    result = preview_initialization_config(
        make_config().model_dump(mode="json")
    )
    assert result["valid"] is True
    assert {
        item["id"] for item in result["identity_core_memory"]["items"]
    } == {
        "core_world_identity",
        "core_player_identity",
        "core_story_expectation",
        "core_failure_severity",
    }
