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
from app.domain.core_memory.campaign_setup import CampaignSetupProposalWire, PublicPowerBaselineWire
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
        peoples_and_beings=["人类", "精灵"],
        major_world_conflicts=["帝国扩张挤压边境诸国，长期抵抗仍在持续。"],
        dominant_powers=["北方帝国", "圣辉教会"],
        civilization_baseline="以马车、冷兵器和手工业为主，火药武器刚刚出现。",
    )


def test_init_config_requires_details_for_custom_ability_system():
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
        "core_peoples_and_beings",
        "core_major_world_conflicts",
        "core_dominant_powers",
        "core_civilization_baseline",
    }
    assert by_id["core_story_expectation"].source == "player"
    assert by_id["core_currency_system"].source == "campaign_setup"
    assert "少数人掌握" in by_id["core_public_power_baseline"].content
    assert "不能复活死者" in by_id["core_public_power_baseline"].content
    assert "人类、精灵" in by_id["core_peoples_and_beings"].content
    assert "北方帝国、圣辉教会" in by_id["core_dominant_powers"].content


def test_world_premise_projection_drops_protagonist_and_player_abilities():
    memory = build_core_memory(
        make_config(
            protagonist_abilities=[
                {"name": "执念拓印", "description": "应承心愿即立因果契约"}
            ]
        ),
        make_proposal(),
    )
    premise = memory.world_premise()
    by_id = {item.id: item for item in premise.items}
    assert set(by_id) == {
        "core_world_identity",
        "core_currency_system",
        "core_public_power_baseline",
        "core_peoples_and_beings",
        "core_major_world_conflicts",
        "core_dominant_powers",
        "core_civilization_baseline",
    }
    assert "core_player_identity" not in by_id
    assert "core_player_abilities" not in by_id
    assert "core_story_expectation" not in by_id
    assert "core_failure_severity" not in by_id
    assert by_id["core_public_power_baseline"].source == "campaign_setup"


def test_campaign_setup_agent_generates_only_public_world_conventions():
    config = make_config()
    mock = MockLLM()
    mock.set_responses([json.dumps({
        "version": 2,
        "currency_unit": "银币",
        "public_power_baseline": {
            "source": "魔法",
            "prevalence": "少数人掌握",
            "learning_difficulty": "困难",
            "limitation": "不能复活死者。",
        },
        "peoples_and_beings": ["人类", "精灵"],
        "major_world_conflicts": ["帝国扩张挤压边境诸国，长期抵抗仍在持续。"],
        "dominant_powers": ["北方帝国", "圣辉教会"],
        "civilization_baseline": "以马车、冷兵器和手工业为主，火药武器刚刚出现。",
    }, ensure_ascii=False)])

    result = CampaignSetupAgent(mock.build_gateway()).generate(
        config=config,
    )
    assert result == make_proposal().model_copy(update={
        "public_power_baseline": result.public_power_baseline,
    })


@pytest.mark.parametrize(
    "limitation",
    [
        "不能复活死者。\n也不能跨越时间。",
        "不能复活死者。也不能跨越时间。",
        "不能复活死者；也不能跨越时间。",
    ],
)
def test_domain_accepts_player_edited_limitation(limitation):
    payload = make_proposal().model_dump(mode="json")
    payload["public_power_baseline"]["limitation"] = limitation
    result = CampaignSetupProposal.model_validate(payload)
    assert result.public_power_baseline.limitation == limitation


def test_domain_accepts_multiline_source_and_civilization():
    payload = make_proposal().model_dump(mode="json")
    payload["public_power_baseline"]["source"] = "魔法\n源脉"
    payload["civilization_baseline"] = "农业为主\n手工业辅助"
    result = CampaignSetupProposal.model_validate(payload)
    assert result.public_power_baseline.source == "魔法\n源脉"
    assert result.civilization_baseline == "农业为主\n手工业辅助"


def test_domain_rejects_overlong_and_non_string_limitation():
    payload = make_proposal().model_dump(mode="json")
    payload["public_power_baseline"]["limitation"] = "边界" * 50
    with pytest.raises(ValidationError):
        CampaignSetupProposal.model_validate(payload)
    payload["public_power_baseline"]["limitation"] = ["不能复活死者。"]
    with pytest.raises(ValidationError):
        CampaignSetupProposal.model_validate(payload)


def test_domain_rejects_blank_civilization_baseline():
    payload = make_proposal().model_dump(mode="json")
    payload["civilization_baseline"] = "   "
    with pytest.raises(ValidationError):
        CampaignSetupProposal.model_validate(payload)


@pytest.mark.parametrize(
    "limitation",
    [
        "不能复活死者。\n也不能跨越时间。",
        "不能复活死者。也不能跨越时间。",
        "不能复活死者；也不能跨越时间。",
    ],
)
def test_generation_wire_still_rejects_limitation_shaping(limitation):
    with pytest.raises(ValidationError):
        PublicPowerBaselineWire.model_validate({
            "source": "魔法",
            "prevalence": "少数人掌握",
            "learning_difficulty": "困难",
            "limitation": limitation,
        })


def test_generation_wire_rejects_blank_civilization_baseline():
    payload = make_proposal().model_dump(mode="json")
    payload["civilization_baseline"] = "   "
    with pytest.raises(ValidationError):
        CampaignSetupProposalWire.model_validate(payload)


def test_generation_wire_rejects_blank_currency_unit():
    payload = make_proposal().model_dump(mode="json")
    payload["currency_unit"] = "   "
    with pytest.raises(ValidationError):
        CampaignSetupProposalWire.model_validate(payload)


def test_campaign_setup_keeps_realistic_world_subjects_minimal():
    proposal = make_proposal().model_copy(update={
        "peoples_and_beings": ["人类"],
        "major_world_conflicts": ["贫富差距长期影响城市居民的生活机会。"],
        "dominant_powers": ["市政府", "大型企业"],
        "civilization_baseline": "现代城市基础设施和消费级数字技术日常可用。",
    })
    assert proposal.peoples_and_beings == ["人类"]


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
