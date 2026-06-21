import uuid

from app.core.llm.config import create_gateway
from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.roll.schemas import Difficulty, RollConfig
from app.domain.roll.resolver import RollResolver
from app.workflows.context_assembler import assemble_context
from app.workflows.evaluate_roll_modifiers import evaluate_roll_modifiers_workflow
from app.workflows.generate_plot_outline import generate_plot_outline_workflow
from app.workflows.generate_scene import (
    build_entity_retrieval_anchors,
    generate_scene_workflow,
    select_required_memories,
)
from app.workflows.pace_narrative import pace_narrative_workflow
from app.workflows.resolve_outcomes import resolve_outcomes_workflow
from app.workflows.resolve_rolls import resolve_rolls_workflow
from app.workflows.retrieve_candidates import retrieve_candidates_workflow
from app.workflows.update_entity_profiles import gather_entity_profiles_from_state_and_anchors

from .serialization import model_data
from .session import PENDING_TURNS, PendingTurn
from .stores import (
    core_memory_store,
    current_state_store,
    entity_store,
    memory_store,
    pending_memory_store,
    scene_summary_store,
    short_term_memory_store,
)


def recent_narrative_types(short_term_memory) -> list[str]:
    return [
        scene.narrative_type
        for scene in short_term_memory.recent_scenes[-2:]
        if scene.narrative_type
    ]


def generate_turn_stream(
    user_input: str,
    difficulty: Difficulty = "困难",
    max_chars: int = 1500,
):
    gateway = create_gateway()
    trace_id = str(uuid.uuid4())
    core_memory = core_memory_store().load_required()

    with current_state_store() as state_store:
        current_state = state_store.load_or_default()

    with short_term_memory_store() as short_store:
        short_term_memory = short_store.get_recent()

    with scene_summary_store() as summary_store:
        scene_summaries = summary_store.list_active()
        recent_summaries = summary_store.get_recent(5)

    yield {
        "step": "state_loaded",
        "data": {
            "trace_id": trace_id,
            "state_before": model_data(current_state),
            "core_memory": model_data(core_memory),
            "short_term_memory": model_data(short_term_memory),
            "scene_summaries": model_data(scene_summaries),
            "recent_summaries": model_data(recent_summaries),
        },
    }

    parser = ActionParser(gateway)
    anchor_result = parser.extract_anchors(
        user_input=user_input,
        current_state=current_state,
        core_memory=core_memory,
        trace_id=trace_id,
    )
    with entity_store() as ent_store:
        action_entity_profiles = gather_entity_profiles_from_state_and_anchors(
            current_state=current_state,
            anchors=anchor_result.anchors,
            entity_store=ent_store,
        )
    action_entity_profile_views = [
        profile.semantic_view()
        for profile in action_entity_profiles
    ]

    yield {
        "step": "entity_profiles_loaded",
        "data": {
            "anchors": model_data(anchor_result.anchors),
            "entity_profiles": model_data(action_entity_profile_views),
        },
    }

    action_result = parser.extract_actions(
        user_input=user_input,
        current_state=current_state,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        entity_profiles=action_entity_profile_views,
        trace_id=trace_id,
    )
    raw_action_parse_result = ActionParseResult(
        analysis=action_result.analysis,
        turn_intent=action_result.turn_intent,
        atomic_actions=action_result.atomic_actions,
        anchors=anchor_result.anchors,
        ambiguities=[
            *action_result.ambiguities,
            *anchor_result.ambiguities,
        ],
    )
    action_self_check = ActionSelfCheck()
    action_parse_result = action_self_check.check(
        raw_action_parse_result,
        user_input,
        current_state,
    )

    yield {
        "step": "action_parsed",
        "data": {
            "raw_action_parse_result": model_data(raw_action_parse_result),
            "action_self_check_result": model_data(action_self_check.last_result),
            "action_parse_result": model_data(action_parse_result),
        },
    }

    with memory_store() as mem_store, pending_memory_store() as pending_store:
        retrieved_memories = retrieve_candidates_workflow(
            action_parse_result,
            mem_store,
            pending_memory_store=pending_store,
        )

    yield {
        "step": "retrieval_done",
        "data": {
            "retrieved_memories": model_data(retrieved_memories),
        },
    }

    roll_config = RollConfig(seed=None, difficulty=difficulty)
    roll_resolver = RollResolver(roll_config)
    roll_actions = roll_resolver.select_roll_actions(action_parse_result)

    modifier_batch = evaluate_roll_modifiers_workflow(
        action_parse_result,
        current_state,
        gateway,
        core_memory=core_memory,
        roll_actions=roll_actions,
        trace_id=trace_id,
    )

    yield {
        "step": "modifiers_done",
        "data": {
            "roll_actions": model_data(roll_actions),
            "modifiers": model_data(modifier_batch),
        },
    }

    roll_results = resolve_rolls_workflow(
        action_parse_result,
        config=roll_config,
        modifier_batch=modifier_batch,
        roll_actions=roll_actions,
    )

    yield {
        "step": "rolls_done",
        "data": {
            "roll_results": model_data(roll_results),
        },
    }

    outcome_resolution = resolve_outcomes_workflow(
        action_parse_result,
        roll_results,
        current_state,
        retrieved_memories=retrieved_memories,
    )

    yield {
        "step": "outcomes_done",
        "data": {
            "outcome_resolution": model_data(outcome_resolution),
        },
    }

    narrative_pacing = pace_narrative_workflow(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        outcome_resolution=outcome_resolution,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        entity_profiles=action_entity_profile_views,
        gateway=gateway,
        previous_types=recent_narrative_types(short_term_memory),
        trace_id=trace_id,
    )

    yield {
        "step": "pacing_done",
        "data": {
            "narrative_pacing": model_data(narrative_pacing),
        },
    }

    context = assemble_context(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        outcome_resolution=outcome_resolution,
        narrative_pacing=narrative_pacing,
        core_memory=core_memory,
        retrieved_memories=retrieved_memories,
        scene_summaries=scene_summaries,
        entity_profiles=action_entity_profile_views,
    )

    yield {
        "step": "context_assembled",
        "data": {
            "context": model_data(context),
        },
    }

    outline = generate_plot_outline_workflow(
        context=context,
        gateway=gateway,
        trace_id=trace_id,
    )

    yield {
        "step": "outline_done",
        "data": {
            "plot_outline": model_data(outline),
        },
    }

    selected_memories = select_required_memories(outline, retrieved_memories)
    selected_memory_ids = [candidate.memory_id for candidate in selected_memories]
    beat_entity_anchors = build_entity_retrieval_anchors(outline)
    with entity_store() as ent_store:
        writer_entity_profiles = gather_entity_profiles_from_state_and_anchors(
            current_state=current_state,
            anchors=[*action_parse_result.anchors, *beat_entity_anchors],
            entity_store=ent_store,
        )
    writer_entity_profile_views = [
        profile.semantic_view()
        for profile in writer_entity_profiles
    ]
    recent_scene = (
        short_term_memory.recent_scenes[-1]
        if short_term_memory.recent_scenes
        else None
    )
    next_turn_index = (recent_scene.turn_index + 1) if recent_scene else 1

    with memory_store() as mem_store:
        scene_result = generate_scene_workflow(
            outline=outline,
            current_state=current_state,
            core_memory=core_memory,
            retrieved_memories=retrieved_memories,
            short_term_memory=short_term_memory,
            gateway=gateway,
            turn_index=next_turn_index,
            entity_profiles=writer_entity_profile_views,
            memory_store=mem_store,
            max_chars=max_chars,
            trace_id=trace_id,
        )

    turn_id = str(uuid.uuid4())
    PENDING_TURNS[turn_id] = PendingTurn(
        turn_id=turn_id,
        user_input=user_input,
        state_before=current_state,
        context=context,
        outline=outline,
        scene_result=scene_result,
        selected_memory_ids=selected_memory_ids,
        turn_index=next_turn_index,
        short_term_memory=short_term_memory,
    )

    yield {
        "step": "scene_written",
        "data": {
            "turn_id": turn_id,
            "trace_id": trace_id,
            "turn_index": next_turn_index,
            "selected_memory_ids": selected_memory_ids,
            "selected_memories": model_data(selected_memories),
            "writer_entity_profiles": model_data(writer_entity_profile_views),
            "recent_scene": model_data(recent_scene),
            "scene_result": model_data(scene_result),
            "context": model_data(context),
            "status": "awaiting_confirm",
        },
    }
