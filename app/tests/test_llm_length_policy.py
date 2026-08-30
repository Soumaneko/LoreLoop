"""Representative checks for the shared LLM H/A/S length policy."""

import json

import pytest
from pydantic import BaseModel, Field

from app.core.llm import MockLLM
from app.core.llm.schema import display_schema_json, schema_json
from app.domain.action.schemas import ActionExtractionResult, ActionRetrievalTopic
from app.domain.action_preview.schemas import ActionPreview, ActionPreviewResultDisplay
from app.domain.continuity.schemas import (
    SUBJECT_ACCEPT_LIMIT,
    SUBJECT_HARD_LIMIT,
    SUBJECT_SOFT_LIMIT,
    ContinuityItemDraft,
    ContinuityProjectionOutput,
)
from app.domain.director.schemas import (
    DIRECTOR_REASONING_ACCEPT_LIMIT,
    DIRECTOR_REASONING_HARD_LIMIT,
    DIRECTOR_REASONING_SOFT_LIMIT,
    DirectorDecisionBatch,
)
from app.domain.director.agent import DirectorAgent
from app.domain.director.skills import DirectorSkillService
from app.domain.director.skill_specs import IntroduceNPCArguments
from app.domain.core_memory.campaign_setup import CampaignSetupProposal
from app.domain.director_npc.schemas import (
    ABILITY_NAME_ACCEPT_LIMIT,
    ABILITY_NAME_HARD_LIMIT,
    DirectorNPCCreateOutput,
    KnownAbility,
)
from app.domain.npc_profile.turn_contract import (
    NPC_SCENE_SUMMARY_ACCEPT_LIMIT,
    NPC_SCENE_SUMMARY_HARD_LIMIT,
    NPC_SCENE_SUMMARY_SOFT_LIMIT,
    NPCSceneSummaryDraft,
    NPCBatchUpdateDisplay,
)
from app.domain.opening_foundation.agent import OpeningFoundationAgent
from app.domain.world_setting.schemas import SettingCreateOutput
from app.domain.core_memory.schemas import CoreMemory
from app.tests.factories.director import make_director_observation


class _OptionalTextProbe(BaseModel):
    note: str | None = Field(default=None, max_length=10)


def _deref(schema: dict, node: dict) -> dict:
    ref = node.get("$ref")
    if isinstance(ref, str):
        return schema["$defs"][ref.split("/")[-1]]
    return node


def test_display_schema_updates_optional_string_constraint():
    displayed = display_schema_json(
        _OptionalTextProbe,
        max_lengths={"note": 8},
    )
    assert _schema_max_length(displayed, "note") == 8


def _schema_max_length(schema_json_text: str, *path: str) -> int | None:
    """在展示 schema 中按属性路径解析 ``maxLength``（跟随 $ref / items / anyOf）。"""

    schema = json.loads(schema_json_text)

    def node_max_length(node: dict) -> int | None:
        if "maxLength" in node:
            return node["maxLength"]
        items = node.get("items")
        if isinstance(items, dict) and "maxLength" in items:
            return items["maxLength"]
        values = {
            value
            for key in ("anyOf", "allOf", "oneOf")
            for option in node.get(key, []) or []
            if isinstance(option, dict)
            if (value := node_max_length(option)) is not None
        }
        assert len(values) <= 1, f"inconsistent maxLength values: {values}"
        return next(iter(values), None)

    def resolve(node: dict | None, parts: list[str]) -> dict | None:
        if node is None or not parts:
            return node
        node = _deref(schema, node)
        properties = node.get("properties", {})
        spec = properties.get(parts[0])
        if spec is None:
            candidates = []
            if isinstance(node.get("items"), dict):
                candidates.append(node["items"])
            for key in ("anyOf", "oneOf", "allOf"):
                candidates.extend(
                    option for option in node.get(key, []) or []
                    if isinstance(option, dict)
                )
            for candidate in candidates:
                found = resolve(_deref(schema, candidate), parts)
                if found is not None:
                    return found
            return None
        if len(parts) == 1:
            return spec
        candidates = []
        if "$ref" in spec:
            candidates.append(spec)
        if isinstance(spec.get("items"), dict):
            candidates.append(spec["items"])
        for key in ("anyOf", "oneOf", "allOf"):
            for option in spec.get(key, []) or []:
                if isinstance(option, dict) and "$ref" in option:
                    candidates.append(option)
        for candidate in candidates:
            found = resolve(_deref(schema, candidate), parts[1:])
            if found is not None:
                return found
        return None

    root = schema
    if "$ref" in root:
        root = _deref(schema, root)
    elif isinstance(root.get("items"), dict):
        root = _deref(schema, root["items"])
    found = resolve(root, list(path))
    if found is not None:
        return node_max_length(found)
    # Fallback: union roots spread fields over several $defs; require a
    # consistent maxLength for the field path across every definition.
    values = set()
    for definition in schema.get("$defs", {}).values():
        spec = resolve(definition, list(path))
        if spec is not None and (value := node_max_length(spec)) is not None:
            values.add(value)
    if not values:
        return None
    assert len(values) == 1, f"inconsistent maxLength values: {values}"
    return values.pop()


#: (说明, 展示 schema JSON 生成器, 字段路径, kind, H, A, S, 领域模型, 领域路径)
#: 仅保留少量代表性条目验证通用 H/A/S 规则，不再镜像全部 LLM 输出字段。
POLICY_ENTRIES = [
    ("continuity.subject", lambda: display_schema_json(
        ContinuityProjectionOutput, max_lengths={}), ("item", "subject"),
     "soft_text", SUBJECT_HARD_LIMIT, SUBJECT_ACCEPT_LIMIT, SUBJECT_SOFT_LIMIT,
     ContinuityItemDraft, ("subject",)),
    ("npc_batch_update.summary", lambda: schema_json(NPCBatchUpdateDisplay),
     ("updates", "scene_summary", "summary"),
     "soft_text", NPC_SCENE_SUMMARY_HARD_LIMIT,
     NPC_SCENE_SUMMARY_ACCEPT_LIMIT, NPC_SCENE_SUMMARY_SOFT_LIMIT,
     NPCSceneSummaryDraft, ("summary",)),
    ("director_decide.reasoning", lambda: display_schema_json(
        DirectorDecisionBatch,
        max_lengths={"reasoning": DIRECTOR_REASONING_HARD_LIMIT}),
     ("reasoning",),
     "soft_text", DIRECTOR_REASONING_HARD_LIMIT,
     DIRECTOR_REASONING_ACCEPT_LIMIT, DIRECTOR_REASONING_SOFT_LIMIT,
     DirectorDecisionBatch, ("reasoning",)),
    ("director_npc_create.ability.name", lambda: display_schema_json(
        DirectorNPCCreateOutput,
        max_lengths={("shell", "known_abilities", "name"): 20}),
     ("shell", "known_abilities", "name"),
     "strict_name", ABILITY_NAME_HARD_LIMIT, ABILITY_NAME_ACCEPT_LIMIT,
     ABILITY_NAME_ACCEPT_LIMIT, KnownAbility, ("name",)),
    ("campaign_setup.currency_unit", lambda: schema_json(
        CampaignSetupProposal), ("currency_unit",),
     "strict_name", 40, 40, 40, CampaignSetupProposal, ("currency_unit",)),
    ("world_setting.name", lambda: schema_json(SettingCreateOutput),
     ("name",), "strict_name", 10, 10, 10, SettingCreateOutput, ("name",)),
    ("action_extract.retrieval_topics[].text",
     lambda: schema_json(ActionExtractionResult),
     ("retrieval_topics", "text"),
     "strict_label", 24, 24, 24, ActionRetrievalTopic, ("text",)),
    ("action_preview.previews[].level",
     lambda: schema_json(ActionPreviewResultDisplay),
     ("previews", "level"),
     "strict_label", 4, 4, 4, ActionPreview, ("level",)),
]


@pytest.mark.parametrize(
    "label,schema_factory,path,kind,h,a,s,domain_model,domain_path",
    POLICY_ENTRIES,
    ids=[entry[0] for entry in POLICY_ENTRIES],
)
def test_length_policy_entry(
    label,
    schema_factory,
    path,
    kind,
    h,
    a,
    s,
    domain_model,
    domain_path,
):
    assert h <= a, f"{label}: H must be <= A"
    if kind == "soft_text":
        assert a <= s, f"{label}: A must be <= S for soft_text"
    else:
        assert kind in ("strict_name", "strict_label"), (
            f"{label}: unknown kind {kind}"
        )
        assert s == a, f"{label}: strict fields have no truncation beyond A"

    displayed = schema_factory()
    if path:
        max_length = _schema_max_length(displayed, *path)
        assert max_length is not None, (
            f"{label}: prompt schema exposes no maxLength at {path}"
        )
        assert max_length == h, (
            f"{label}: prompt schema maxLength {max_length} != target H {h}"
        )
    domain_max = _schema_max_length(schema_json(domain_model), *domain_path)
    assert domain_max is None or domain_max >= s, (
        f"{label}: final domain maxLength {domain_max} < salvage limit S {s}"
    )


@pytest.mark.parametrize(
    "guidance",
    [
        "让人物自然进入场景。",
        {"name": "阿岚", "purpose": "引入人物", "description": "补充细节"},
    ],
)
def test_spontaneous_npc_accept_limit_retries_for_every_guidance_shape(guidance):
    service = DirectorSkillService()
    service.register(
        name="introduce_npc",
        description="引入人物",
        phases={"generation"},
        mutates=False,
        arguments_model=IntroduceNPCArguments,
        handler=lambda arguments, guidance=None: None,
    )

    def response(trait_length: int) -> str:
        return json.dumps({
            "reasoning": "需要一名路人。",
            "requests": [{
                "request_id": "request_1",
                "skill": "introduce_npc",
                "arguments": {
                    "npc_id": None,
                    "spontaneous": {
                        "name": "阿岚",
                        "role": "路人",
                        "appearance": "普通装束",
                        "personality_trait": "稳" * trait_length,
                        "known_abilities": [],
                    },
                    "view_mode": "face_to_face",
                },
                "guidance": guidance,
            }],
        }, ensure_ascii=False)

    mock = MockLLM()
    mock.set_responses([response(51), response(60)])
    result = DirectorAgent(mock.build_gateway(), service).decide_generation(
        make_director_observation()
    )

    assert mock.provider._index == 2
    assert len(
        result.requests[0].arguments["spontaneous"]["personality_trait"]
    ) == 60


def test_spontaneous_ability_description_retries_then_truncates():
    service = DirectorSkillService()
    service.register(
        name="introduce_npc",
        description="引入人物",
        phases={"generation"},
        mutates=False,
        arguments_model=IntroduceNPCArguments,
        handler=lambda arguments, guidance=None: None,
    )

    def response(description_length: int) -> str:
        return json.dumps({
            "reasoning": "需要一名医师。",
            "requests": [{
                "request_id": "request_1",
                "skill": "introduce_npc",
                "arguments": {
                    "npc_id": None,
                    "spontaneous": {
                        "name": "阿岚",
                        "role": "医师",
                        "appearance": "普通装束",
                        "personality_trait": "谨慎",
                        "known_abilities": [{
                            "name": "急救",
                            "description": "医" * description_length,
                        }],
                    },
                    "view_mode": "face_to_face",
                },
                "guidance": "让人物自然进入场景。",
            }],
        }, ensure_ascii=False)

    mock = MockLLM()
    mock.set_responses([response(151), response(181)])
    agent = DirectorAgent(mock.build_gateway(), service)
    result = agent.decide_generation(make_director_observation())

    assert mock.provider._index == 2
    description = result.requests[0].arguments["spontaneous"][
        "known_abilities"
    ][0]["description"]
    assert len(description) == 180
    assert any(
        warning.code == "director_spontaneous_truncated"
        for warning in agent.last_warnings
    )


def test_opening_foundation_blank_situation_retries_inside_gateway():
    first = {
        "version": 1,
        "starting_location": {"name": "码头", "description": "潮湿的旧码头。"},
        "protagonist_opening_situation": " ",
        "initial_resources": {"inventory": [], "money": 0, "fixed_assets": []},
    }
    second = {**first, "protagonist_opening_situation": "主角刚刚抵达码头。"}
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    result = OpeningFoundationAgent(mock.build_gateway()).generate(
        core_memory=CoreMemory(items=[])
    )

    assert mock.provider._index == 2
    assert result.protagonist_opening_situation == "主角刚刚抵达码头。"
