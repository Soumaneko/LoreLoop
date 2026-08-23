from app.core.llm import PromptLoader


def test_director_agent_prompt_is_chinese():
    generation_prompt, _ = PromptLoader().load("director_agent_decide_generation")
    confirmation_prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    for prompt in (generation_prompt, confirmation_prompt):
        assert "导演协调 Agent" in prompt
        assert "只输出符合 Schema 的 JSON" in prompt


def test_director_decide_prompt_describes_guidance():
    generation_prompt, _ = PromptLoader().load("director_agent_decide_generation")
    confirmation_prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    for prompt in (generation_prompt, confirmation_prompt):
        assert "`guidance`" in prompt
        assert "方向性指导意见" in prompt
        assert "不得在 `guidance` 中撰写具体正文" in prompt


def test_director_decide_prompts_are_phase_separated():
    generation_prompt, _ = PromptLoader().load("director_agent_decide_generation")
    confirmation_prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    assert "生成阶段每个技能请求都必须填写非空 `guidance`" in generation_prompt
    assert "`view_mode`" in generation_prompt
    assert "create_event" not in generation_prompt

    assert "世界设定的绝对依赖性" in confirmation_prompt
    assert "`create_event`" in confirmation_prompt
    assert "`setting_requirements`" in confirmation_prompt
    assert "`view_mode`" not in confirmation_prompt


def test_director_decide_prompt_defines_create_event_boundary():
    prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    assert "create_event" in prompt
    assert "`setting_requirements`" in prompt
    assert "`create_npc`" in prompt
    assert "`enrich_npc`" not in prompt
    assert "source=existing" in prompt
    assert "source=new" in prompt


def test_director_decide_prompt_forbids_quest_wrapping():
    prompt, _ = PromptLoader().load("director_agent_decide_confirmation")

    assert "世界设定的绝对依赖性" in prompt
    assert "严禁凭空捏造未备案的专有名词" in prompt
    assert "长草期" in prompt


def test_event_skill_descriptions_forbid_quest_wrapping():
    from app.domain.director.skill_specs import get_director_skill_specs

    specs = {
        spec.name: spec
        for spec in get_director_skill_specs()
        if spec.name in {"create_event"}
    }
    for spec in specs.values():
        assert "主角视线之外" in spec.description
        assert "不得用于推动、包装或代劳主角当前正在执行的主线行动" in spec.description
