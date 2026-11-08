import json

import pytest

from app.core.llm.gateway import LLMGateway
from app.core.llm.prompts import PromptLoader
from app.core.llm.providers import MockProvider
from app.core.llm.tracing import NullTraceLogger
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.narrative_composer.runner import run_narrative_composition
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.action_judgment.schemas import ActionJudgment
from app.domain.npc_profile.schemas import NPCProfileDirectorView


def _context() -> NarrativeComposerContext:
    return NarrativeComposerContext(
        user_input="观察四周",
        current_state=CurrentState(
            scene=SceneState(scene_id="s1", location="广场", participants=["萧欢"]),
            player=PlayerState(name="萧欢"),
        ),
        action_judgments=[ActionJudgment(
            action_id="A1",
            sequence=1,
            actor=["萧欢"],
            action_type="observe",
            action_text="观察四周",
            resolution_source="automatic",
            outcome_band="success",
        )],
        core_memory=CoreMemory(items=[]),
    )


def _gateway(output: dict) -> LLMGateway:
    provider = MockProvider()
    provider.set_responses([json.dumps(output, ensure_ascii=False)] * 3)
    return LLMGateway(
        provider=provider,
        prompt_loader=PromptLoader(),
        tracer=NullTraceLogger(),
    )


def _output() -> dict:
    return {
        "director_reason": None,
        "action_resolutions": [{
            "action_id": "A1",
            "outcome_band": "success",
            "resolved_fact": "你看清了广场周围的动静。",
        }],
        "scene_stopping_point": "广场仍有数处动静等待回应。",
        "planned_events": [{
            "event_id": "e1",
            "event_fact": "你观察广场，周围的人群作出反应。",
            "linked_outcome_ids": ["A1"],
            "involved_entities": [],
        }],
        "scene_reasoning": "落实成功观察并保持局面开放。",
        "scene_text": "你环顾广场，人群的动静逐一落入眼中。",
        "npc_usages": [],
        "director_usages": [],
        "closing_prompt": "人群仍在流动，你会怎么做？",
        "next_action_directions": [],
    }


@pytest.mark.parametrize("presence", ["appeared", "mentioned_only"])
def test_composer_accepts_matching_program_judgment(presence):
    context = _context()
    context.npc_profiles = [NPCProfileDirectorView(name="赵七")]
    output = _output()
    output["scene_text"] += "中年汉子，老赵。" if presence == "appeared" else "你听说了中年汉子老赵的事。"
    # Surplus archive names stay available for offline review without rejecting prose.
    output["npc_usages"] = [{"name": "赵七", "surfaces": ["中年汉子", "老赵", "赵七"], "presence": presence}]
    output["director_usages"] = ["event_1", "unmatched"]
    result = run_narrative_composition(_gateway(output), context=context)
    assert result.action_resolutions[0].outcome_band == "success"
    assert result.scene_text.startswith("你环顾广场")
    assert result.npc_usages[0].presence == presence
    assert result.npc_usages[0].name == "赵七"
    assert "赵七" not in result.scene_text
    assert "赵七" in result.npc_usages[0].surfaces
    scene = result.to_scene_result()
    assert scene.npc_usages == result.npc_usages
    assert result.director_usages == output["director_usages"]
    assert scene.director_usages_for_text(scene.scene_text) == result.director_usages
    assert scene.director_usages_for_text(scene.scene_text + "编辑后的正文") == []
    legacy = scene.model_dump(exclude={"director_usages", "npc_usages_text_hash"})
    assert type(scene).model_validate(legacy).director_usages_for_text(scene.scene_text) == []


def test_composer_rejects_outcome_reversal():
    output = _output()
    output["action_resolutions"][0]["outcome_band"] = "failure"
    with pytest.raises(Exception, match="outcome_band mismatch"):
        run_narrative_composition(_gateway(output), context=_context())
