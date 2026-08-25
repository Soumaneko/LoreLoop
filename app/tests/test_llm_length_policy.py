"""Issue 07 防遗漏门禁：LLM 输出长度策略清单。

清单记录 ``task + field path + kind + H/A/S``，并递归校验：

1. 每个受限 LLM 输出字段都有分类；
2. ``soft_text`` 满足 ``H <= A <= S``；
3. ``strict_name/strict_label`` 不配置截断（S 缺省或等于 A）；
4. ``identifier/reference/enum`` 不进入本清单（不配缓冲/截断）；
5. Prompt 展示 Schema 的 ``maxLength == H``；
6. 最终领域模型能接收该策略可能写入的最大合法长度 ``S``。
"""

import json

import pytest
from pydantic import BaseModel, Field

from app.core.llm import MockLLM
from app.core.llm.schema import display_schema_json, schema_json
from app.domain.action.schemas import ActionExtractionResult, ActionRetrievalTopic
from app.domain.action_preview.schemas import ActionPreview, ActionPreviewResultDisplay
from app.domain.continuity.schemas import (
    PREMISE_ACCEPT_LIMIT,
    PREMISE_HARD_LIMIT,
    PREMISE_SOFT_LIMIT,
    REASON_ACCEPT_LIMIT,
    REASON_HARD_LIMIT,
    REASON_SOFT_LIMIT,
    STATE_DELTA_ACCEPT_LIMIT,
    STATE_DELTA_HARD_LIMIT,
    STATE_DELTA_SOFT_LIMIT,
    SUBJECT_ACCEPT_LIMIT,
    SUBJECT_HARD_LIMIT,
    SUBJECT_SOFT_LIMIT,
    CURRENT_STATE_HARD_LIMIT,
    CURRENT_STATE_ACCEPT_LIMIT,
    CURRENT_STATE_SOFT_LIMIT,
    ContinuityProjectionOutput,
)
from app.domain.director.schemas import (
    DIRECTOR_REASONING_ACCEPT_LIMIT,
    DIRECTOR_REASONING_HARD_LIMIT,
    DIRECTOR_REASONING_SOFT_LIMIT,
    GUIDANCE_NAME_ACCEPT_LIMIT,
    GUIDANCE_NAME_HARD_LIMIT,
    GUIDANCE_PURPOSE_ACCEPT_LIMIT,
    GUIDANCE_PURPOSE_HARD_LIMIT,
    GUIDANCE_PURPOSE_SOFT_LIMIT,
    DirectorDecisionBatch,
    DirectorGuidance,
)
from app.domain.director.agent import DirectorAgent
from app.domain.director.skills import DirectorSkillService
from app.domain.director.skill_specs import IntroduceNPCArguments
from app.domain.core_memory.campaign_setup import (
    CAMPAIGN_SETUP_PROMPT_TARGETS,
    CampaignSetupProposal,
    PublicPowerBaseline,
)
from app.domain.director_npc.schemas import (
    ABILITY_DESCRIPTION_ACCEPT_LIMIT,
    ABILITY_DESCRIPTION_HARD_LIMIT,
    ABILITY_DESCRIPTION_SOFT_LIMIT,
    ABILITY_NAME_ACCEPT_LIMIT,
    ABILITY_NAME_HARD_LIMIT,
    DirectorNPCBehavior,
    DirectorNPCCreateOutput,
    KnownAbility,
)
from app.domain.npc_profile.turn_contract import (
    CHANGE_BASIS_ACCEPT_LIMIT,
    CHANGE_BASIS_HARD_LIMIT,
    CHANGE_BASIS_SOFT_LIMIT,
    NPC_SCENE_SUMMARY_ACCEPT_LIMIT,
    NPC_SCENE_SUMMARY_HARD_LIMIT,
    NPC_SCENE_SUMMARY_SOFT_LIMIT,
    NPCSceneSummaryDraft,
    NPCBatchUpdateDisplay,
    ScalarStableChange,
)
from app.domain.opening_foundation.agent import OPENING_FOUNDATION_PROMPT_TARGETS
from app.domain.opening_foundation.agent import OpeningFoundationAgent
from app.domain.opening_foundation.schemas import OpeningFoundation, OpeningLocation
from app.domain.opening_setting.schemas import OpeningSettingSpec, OpeningSettingSpecs
from app.domain.director_event.schemas import (
    DirectorEventActorRole,
    DirectorEventSelectionOutput,
)
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.turn_resolution.resolver import _TurnResolutionOutput
from app.domain.turn_resolution.schemas import ActionResolutionFrame
from app.domain.world_setting.schemas import SettingCreateOutput
from app.domain.world_setting.creator import SETTING_PROMPT_TARGETS
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


#: (说明, 展示 schema JSON 生成器, 字段路径, kind, H, A, S)
POLICY_ENTRIES = [
    # --- Continuity（soft_text）---
    ("continuity.subject", lambda: display_schema_json(
        ContinuityProjectionOutput, max_lengths={}), ("item", "subject"),
     "soft_text", SUBJECT_HARD_LIMIT, SUBJECT_ACCEPT_LIMIT, SUBJECT_SOFT_LIMIT),
    ("continuity.premise", lambda: display_schema_json(
        ContinuityProjectionOutput, max_lengths={}), ("item", "premise"),
     "soft_text", PREMISE_HARD_LIMIT, PREMISE_ACCEPT_LIMIT, PREMISE_SOFT_LIMIT),
    ("continuity.state_delta", lambda: display_schema_json(
        ContinuityProjectionOutput, max_lengths={}), ("state_delta",),
     "soft_text", STATE_DELTA_HARD_LIMIT, STATE_DELTA_ACCEPT_LIMIT,
     STATE_DELTA_SOFT_LIMIT),
    ("continuity.new_current_state", lambda: display_schema_json(
        ContinuityProjectionOutput, max_lengths={}), ("new_current_state",),
     "soft_text", CURRENT_STATE_HARD_LIMIT, CURRENT_STATE_ACCEPT_LIMIT,
     CURRENT_STATE_SOFT_LIMIT),
    ("continuity.reason", lambda: display_schema_json(
        ContinuityProjectionOutput, max_lengths={}), ("reason",),
     "soft_text", REASON_HARD_LIMIT, REASON_ACCEPT_LIMIT, REASON_SOFT_LIMIT),
    ("continuity.create.location", lambda: schema_json(
        ContinuityProjectionOutput), ("item", "location"),
     "strict_label", 80, 80, 80),
    ("continuity.create.deadline", lambda: schema_json(
        ContinuityProjectionOutput), ("item", "deadline"),
     "strict_label", 80, 80, 80),
    ("continuity.update.location", lambda: schema_json(
        ContinuityProjectionOutput), ("location",),
     "strict_label", 80, 80, 80),
    ("continuity.update.deadline", lambda: schema_json(
        ContinuityProjectionOutput), ("deadline",),
     "strict_label", 80, 80, 80),
    # --- Turn Resolution（soft_text）---
    ("turn_resolution.effect_reasoning", lambda: schema_json(
        _TurnResolutionOutput), ("action_resolutions", "effect_reasoning"),
     "soft_text", 120, 150, 180),
    ("turn_resolution.resolved_fact", lambda: schema_json(
        _TurnResolutionOutput), ("action_resolutions", "resolved_fact"),
     "soft_text", 120, 150, 180),
    ("turn_resolution.narrative_hook", lambda: schema_json(
        _TurnResolutionOutput), ("action_resolutions", "narrative_hook"),
     "soft_text", 120, 150, 180),
    # --- NPC Batch Update（soft_text）---
    ("npc_batch_update.summary", lambda: schema_json(NPCBatchUpdateDisplay),
     ("updates", "scene_summary", "summary"),
     "soft_text", NPC_SCENE_SUMMARY_HARD_LIMIT,
     NPC_SCENE_SUMMARY_ACCEPT_LIMIT, NPC_SCENE_SUMMARY_SOFT_LIMIT),
    ("npc_batch_update.change_basis", lambda: schema_json(NPCBatchUpdateDisplay),
     ("updates", "stable_changes", "change_basis"),
     "soft_text", CHANGE_BASIS_HARD_LIMIT, CHANGE_BASIS_ACCEPT_LIMIT,
     CHANGE_BASIS_SOFT_LIMIT),
    # --- Scene Summary（soft_text）---
    ("scene_summarize.content", lambda: json.dumps({
        "properties": {"content": {"type": "string", "maxLength": 300}}
    }), ("content",), "soft_text", 300, 375, 450),
    ("scene_summary_condense.content", lambda: json.dumps({
        "properties": {"content": {"type": "string", "maxLength": 400}}
    }), ("content",), "soft_text", 400, 500, 600),
    # --- Director Decide ---
    ("director_decide.reasoning", lambda: display_schema_json(
        DirectorDecisionBatch,
        max_lengths={
            "reasoning": DIRECTOR_REASONING_HARD_LIMIT,
            ("requests", "guidance", "name"): GUIDANCE_NAME_HARD_LIMIT,
            ("requests", "guidance", "purpose"): GUIDANCE_PURPOSE_HARD_LIMIT,
        }), ("reasoning",),
     "soft_text", DIRECTOR_REASONING_HARD_LIMIT,
     DIRECTOR_REASONING_ACCEPT_LIMIT, DIRECTOR_REASONING_SOFT_LIMIT),
    ("director_decide.guidance.name", lambda: display_schema_json(
        DirectorDecisionBatch,
        max_lengths={
            "reasoning": DIRECTOR_REASONING_HARD_LIMIT,
            ("requests", "guidance", "name"): GUIDANCE_NAME_HARD_LIMIT,
            ("requests", "guidance", "purpose"): GUIDANCE_PURPOSE_HARD_LIMIT,
        }), ("requests", "guidance", "name"),
     "strict_name", GUIDANCE_NAME_HARD_LIMIT, GUIDANCE_NAME_ACCEPT_LIMIT,
     GUIDANCE_NAME_ACCEPT_LIMIT),
    ("director_decide.guidance.purpose", lambda: display_schema_json(
        DirectorDecisionBatch,
        max_lengths={
            "reasoning": DIRECTOR_REASONING_HARD_LIMIT,
            ("requests", "guidance", "name"): GUIDANCE_NAME_HARD_LIMIT,
            ("requests", "guidance", "purpose"): GUIDANCE_PURPOSE_HARD_LIMIT,
        }), ("requests", "guidance", "purpose"),
     "soft_text", GUIDANCE_PURPOSE_HARD_LIMIT,
     GUIDANCE_PURPOSE_ACCEPT_LIMIT, GUIDANCE_PURPOSE_SOFT_LIMIT),
    # --- Director NPC Create（soft_text / strict_name）---
    ("director_npc_create.personality_trait", lambda: display_schema_json(
        DirectorNPCCreateOutput,
        max_lengths={("behavior", "personality_trait"): 40}),
     ("behavior", "personality_trait"),
     "soft_text", 40, 50, 60),
    ("director_npc_create.ability.name", lambda: display_schema_json(
        DirectorNPCCreateOutput,
        max_lengths={("shell", "known_abilities", "name"): 20}),
     ("shell", "known_abilities", "name"),
     "strict_name", ABILITY_NAME_HARD_LIMIT, ABILITY_NAME_ACCEPT_LIMIT,
     ABILITY_NAME_ACCEPT_LIMIT),
    ("director_npc_create.ability.description", lambda: display_schema_json(
        DirectorNPCCreateOutput,
        max_lengths={("shell", "known_abilities", "description"): 120}),
     ("shell", "known_abilities", "description"),
     "soft_text", ABILITY_DESCRIPTION_HARD_LIMIT,
     ABILITY_DESCRIPTION_ACCEPT_LIMIT, ABILITY_DESCRIPTION_SOFT_LIMIT),
    # --- P1: Campaign / Opening / Setting（soft_text / strict_name）---
    ("campaign_setup.currency_unit", lambda: schema_json(
        CampaignSetupProposal), ("currency_unit",),
     "strict_name", 40, 40, 40),
    ("campaign_setup.peoples_and_beings[]", lambda: schema_json(
        CampaignSetupProposal), ("peoples_and_beings",),
     "strict_name", 10, 10, 10),
    ("campaign_setup.dominant_powers[]", lambda: schema_json(
        CampaignSetupProposal), ("dominant_powers",),
     "strict_name", 10, 10, 10),
    ("campaign_setup.source", lambda: display_schema_json(
        CampaignSetupProposal, max_lengths=CAMPAIGN_SETUP_PROMPT_TARGETS),
     ("public_power_baseline", "source"), "soft_text", 100, 120, 120),
    ("campaign_setup.limitation", lambda: display_schema_json(
        CampaignSetupProposal, max_lengths=CAMPAIGN_SETUP_PROMPT_TARGETS),
     ("public_power_baseline", "limitation"), "soft_text", 60, 80, 80),
    ("campaign_setup.civilization_baseline", lambda: display_schema_json(
        CampaignSetupProposal, max_lengths=CAMPAIGN_SETUP_PROMPT_TARGETS),
     ("civilization_baseline",), "soft_text", 160, 200, 200),
    ("campaign_setup.major_world_conflicts[]", lambda: display_schema_json(
        CampaignSetupProposal, max_lengths=CAMPAIGN_SETUP_PROMPT_TARGETS),
     ("major_world_conflicts",), "soft_text", 40, 50, 50),
    ("opening_foundation.description", lambda: display_schema_json(
        OpeningFoundation, max_lengths=OPENING_FOUNDATION_PROMPT_TARGETS),
     ("starting_location", "description"), "soft_text", 480, 600, 600),
    ("opening_foundation.location.name", lambda: schema_json(
        OpeningFoundation), ("starting_location", "name"),
     "strict_name", 120, 120, 120),
    ("opening_foundation.situation", lambda: display_schema_json(
        OpeningFoundation, max_lengths=OPENING_FOUNDATION_PROMPT_TARGETS),
     ("protagonist_opening_situation",), "soft_text", 640, 800, 800),
    ("opening_setting.setting_description", lambda: display_schema_json(
        OpeningSettingSpecs,
        max_lengths={("specs", "setting_description"): 100}),
     ("specs", "setting_description"), "soft_text", 100, 120, 120),
    ("world_setting.summary", lambda: display_schema_json(
        SettingCreateOutput, max_lengths=SETTING_PROMPT_TARGETS),
     ("summary",), "soft_text", 80, 100, 100),
    ("world_setting.name", lambda: schema_json(SettingCreateOutput),
     ("name",), "strict_name", 10, 10, 10),
    ("world_setting.public_facts[]", lambda: display_schema_json(
        SettingCreateOutput, max_lengths=SETTING_PROMPT_TARGETS),
     ("public_facts",), "soft_text", 80, 100, 100),
    ("world_setting.hidden_facts[]", lambda: display_schema_json(
        SettingCreateOutput, max_lengths=SETTING_PROMPT_TARGETS),
     ("hidden_facts",), "soft_text", 80, 100, 100),
    # --- P2: 紧凑标签；暂无真实超限重试证据，仅分类登记（H=A，无 S 缓冲）。
    ("action_extract.retrieval_topics[].text",
     lambda: schema_json(ActionExtractionResult),
     ("retrieval_topics", "text"),
     "strict_label", 24, 24, 24),
    ("action_preview.previews[].level",
     lambda: schema_json(ActionPreviewResultDisplay),
     ("previews", "level"),
     "strict_label", 4, 4, 4),
    ("director_event_select.actor_roles[].role_in_event",
     lambda: schema_json(DirectorEventSelectionOutput),
     ("actor_roles", "role_in_event"),
     "soft_text", 200, 200, 200),
]


def _domain_max_length(label: str) -> int | None:
    """Final domain / persistence model must accept the salvaged length S."""

    from app.domain.continuity.schemas import (
        ContinuityItemDraft,
        ContinuityUpdateDraft,
        _OperationBase,
    )
    domain_map: dict[str, tuple[type[BaseModel], tuple[str, ...]]] = {
        "continuity.subject": (ContinuityItemDraft, ("subject",)),
        "continuity.premise": (ContinuityItemDraft, ("premise",)),
        "continuity.state_delta": (ContinuityUpdateDraft, ("state_delta",)),
        "continuity.new_current_state": (
            ContinuityUpdateDraft,
            ("new_current_state",),
        ),
        "continuity.reason": (_OperationBase, ("reason",)),
        "continuity.create.location": (ContinuityItemDraft, ("location",)),
        "continuity.create.deadline": (ContinuityItemDraft, ("deadline",)),
        "continuity.update.location": (ContinuityUpdateDraft, ("location",)),
        "continuity.update.deadline": (ContinuityUpdateDraft, ("deadline",)),
        "turn_resolution.effect_reasoning": (
            ActionResolutionFrame, ("effect_reasoning",),
        ),
        "turn_resolution.resolved_fact": (
            ActionResolutionFrame, ("resolved_fact",),
        ),
        "turn_resolution.narrative_hook": (
            ActionResolutionFrame, ("narrative_hook",),
        ),
        "npc_batch_update.summary": (NPCSceneSummaryDraft, ("summary",)),
        "npc_batch_update.change_basis": (
            ScalarStableChange, ("change_basis",),
        ),
        "scene_summarize.content": (SceneSummary, ("content",)),
        "scene_summary_condense.content": (SceneSummary, ("content",)),
        "director_decide.reasoning": (DirectorDecisionBatch, ("reasoning",)),
        "director_decide.guidance.name": (DirectorGuidance, ("name",)),
        "director_decide.guidance.purpose": (DirectorGuidance, ("purpose",)),
        "director_npc_create.personality_trait": (
            DirectorNPCBehavior, ("personality_trait",),
        ),
        "director_npc_create.ability.name": (KnownAbility, ("name",)),
        "director_npc_create.ability.description": (
            KnownAbility, ("description",),
        ),
        "campaign_setup.currency_unit": (
            CampaignSetupProposal, ("currency_unit",),
        ),
        "campaign_setup.peoples_and_beings[]": (
            CampaignSetupProposal, ("peoples_and_beings",),
        ),
        "campaign_setup.dominant_powers[]": (
            CampaignSetupProposal, ("dominant_powers",),
        ),
        "campaign_setup.source": (PublicPowerBaseline, ("source",)),
        "campaign_setup.limitation": (PublicPowerBaseline, ("limitation",)),
        "campaign_setup.civilization_baseline": (
            CampaignSetupProposal, ("civilization_baseline",),
        ),
        "campaign_setup.major_world_conflicts[]": (
            CampaignSetupProposal, ("major_world_conflicts",),
        ),
        "opening_foundation.location.name": (OpeningLocation, ("name",)),
        "opening_foundation.description": (OpeningLocation, ("description",)),
        "opening_foundation.situation": (
            OpeningFoundation, ("protagonist_opening_situation",),
        ),
        "opening_setting.setting_description": (
            OpeningSettingSpec, ("setting_description",),
        ),
        "world_setting.name": (SettingCreateOutput, ("name",)),
        "world_setting.summary": (SettingCreateOutput, ("summary",)),
        "world_setting.public_facts[]": (
            SettingCreateOutput, ("public_facts",),
        ),
        "world_setting.hidden_facts[]": (
            SettingCreateOutput, ("hidden_facts",),
        ),
        "action_extract.retrieval_topics[].text": (
            ActionRetrievalTopic, ("text",),
        ),
        "action_preview.previews[].level": (ActionPreview, ("level",)),
        "director_event_select.actor_roles[].role_in_event": (
            DirectorEventActorRole, ("role_in_event",),
        ),
    }
    model, path = domain_map[label]
    return _schema_max_length(schema_json(model), *path)


@pytest.mark.parametrize(
    "label,schema_factory,path,kind,h,a,s",
    POLICY_ENTRIES,
    ids=[entry[0] for entry in POLICY_ENTRIES],
)
def test_length_policy_entry(label, schema_factory, path, kind, h, a, s):
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
    domain_max = _domain_max_length(label)
    assert domain_max is None or domain_max >= s, (
        f"{label}: final domain maxLength {domain_max} < salvage limit S {s}"
    )


def test_no_unregistered_llm_constrained_field():
    """A new maxLength in a migrated production prompt needs classification."""

    expected: list[tuple[type[BaseModel], set[tuple[str, ...]]]] = [
        (ContinuityProjectionOutput, {
            ("reason",), ("item", "subject"), ("item", "premise"),
            ("item", "location"), ("item", "deadline"), ("subject",),
            ("state_delta",), ("new_current_state",), ("location",),
            ("deadline",),
        }),
        (_TurnResolutionOutput, {
            ("action_resolutions", "effect_reasoning"),
            ("action_resolutions", "resolved_fact"),
            ("action_resolutions", "narrative_hook"),
        }),
        (NPCBatchUpdateDisplay, {
            ("updates", "scene_summary", "summary"),
            ("updates", "stable_changes", "change_basis"),
        }),
        (DirectorDecisionBatch, {
            ("reasoning",), ("requests", "request_id"),
            ("requests", "skill"), ("requests", "guidance", "name"),
            ("requests", "guidance", "purpose"),
            ("requests", "guidance", "description"),
        }),
        (DirectorNPCCreateOutput, {
            ("behavior", "personality_trait"),
            ("shell", "known_abilities", "name"),
            ("shell", "known_abilities", "description"),
        }),
        (CampaignSetupProposal, {
            ("currency_unit",), ("public_power_baseline", "source"),
            ("public_power_baseline", "limitation"),
            ("peoples_and_beings",), ("major_world_conflicts",),
            ("dominant_powers",), ("civilization_baseline",),
        }),
        (OpeningFoundation, {
            ("starting_location", "name"),
            ("starting_location", "description"),
            ("protagonist_opening_situation",),
        }),
        (OpeningSettingSpecs, {("specs", "setting_description")}),
        (SettingCreateOutput, {
            ("name",), ("summary",), ("public_facts",), ("hidden_facts",),
        }),
        (ActionExtractionResult, {("retrieval_topics", "text")}),
        (ActionPreviewResultDisplay, {("previews", "level")}),
        (DirectorEventSelectionOutput, {
            ("actor_roles", "actor_key"),
            ("actor_roles", "role_in_event"),
            ("hook_pool", "hook_id"),
        }),
    ]
    for model, known_paths in expected:
        actual_paths = _schema_length_paths(model.model_json_schema())
        assert actual_paths == known_paths, (
            f"{model.__name__} constrained fields changed; classify the diff: "
            f"expected={sorted(known_paths)}, actual={sorted(actual_paths)}"
        )


def _schema_length_paths(schema: dict) -> set[tuple[str, ...]]:
    definitions = schema.get("$defs", {})
    paths: set[tuple[str, ...]] = set()
    visited: set[tuple[str, tuple[str, ...]]] = set()

    def walk(node, path: tuple[str, ...] = ()) -> None:
        if not isinstance(node, dict):
            return
        if path and "maxLength" in node:
            paths.add(path)
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            marker = (name, path)
            if marker not in visited and name in definitions:
                visited.add(marker)
                walk(definitions[name], path)
        for name, spec in node.get("properties", {}).items():
            walk(spec, path + (name,))
        for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
            for child in node.get(key, []) or []:
                walk(child, path)
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, path)

    walk(schema)
    return paths


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
