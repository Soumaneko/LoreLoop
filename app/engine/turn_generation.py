from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
import uuid

from app.core.llm.config import create_gateway
from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.domain.context_qa.schemas import ContextAnswerSet, ContextQuestionInput
from app.domain.roll.schemas import Difficulty, RollConfig
from app.domain.roll.resolver import RollResolver
from app.workflows.context_assembler import assemble_context
from app.workflows.evaluate_roll_modifiers import evaluate_roll_modifiers_workflow
from app.workflows.generate_context_questions import generate_context_questions_workflow
from app.workflows.generate_plot_outline import generate_plot_outline_workflow
from app.workflows.generate_scene import (
    build_entity_retrieval_anchors,
    generate_scene_workflow,
)
from app.workflows.resolve_rolls import resolve_rolls_workflow
from app.workflows.resolve_turn import resolve_turn_workflow
from app.workflows.retrieve_candidates import retrieve_candidates_workflow
from app.workflows.update_director_event import DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS
from app.workflows.director_npc import maybe_director_npc_workflow
from app.workflows.update_entity_profiles import gather_entity_profiles_from_state_and_anchors
from app.domain.npc_profile.workflows import gather_npc_profiles_from_state_and_anchors
from app.workflows.answer_context_questions import answer_context_questions_workflow
from app.workflows.select_context_profiles import select_context_profiles

from .serialization import model_data
from .session import PENDING_TURNS, PendingTurn
from .stores import (
    core_memory_store,
    current_state_store,
    director_event_store,
    director_npc_store,
    entity_store,
    memory_store,
    npc_profile_store,
    scene_summary_store,
    short_term_memory_store,
)


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

    turn_index = (
        (short_term_memory.recent_scenes[-1].turn_index + 1)
        if short_term_memory.recent_scenes
        else 1
    )
    gateway.set_trace_context(turn_index=turn_index, session_type="generation")

    with scene_summary_store() as summary_store:
        scene_summaries = summary_store.list_active()
        recent_summaries = summary_store.get_recent(5)

    with director_event_store() as director_store:
        active_director_event = director_store.get_active()
        recent_archived_director_event = (
            None
            if active_director_event is not None
            else director_store.get_recent_archived(
                turn_index=turn_index,
                cooldown_turns=DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS,
            )
        )

    yield {
        "step": "state_loaded",
        "data": {
            "trace_id": trace_id,
            "state_before": model_data(current_state),
            "core_memory": model_data(core_memory),
            "short_term_memory": model_data(short_term_memory),
            "scene_summaries": model_data(scene_summaries),
            "recent_summaries": model_data(recent_summaries),
            "active_director_event": model_data(active_director_event),
            "recent_archived_director_event": model_data(recent_archived_director_event),
        },
    }

    with entity_store() as ent_store, npc_profile_store() as npc_store:
        context_profile_selection = select_context_profiles(
            user_input=user_input,
            current_state=current_state,
            entity_store=ent_store,
            npc_store=npc_store,
        )
    matched_anchors = context_profile_selection.anchors
    action_entity_profiles = context_profile_selection.entity_profiles
    action_npc_profiles = context_profile_selection.npc_profiles
    action_entity_profile_views = [
        profile.semantic_view()
        for profile in action_entity_profiles
    ]
    action_npc_director_views = [
        profile.director_view()
        for profile in action_npc_profiles
    ]

    yield {
        "step": "entity_profiles_loaded",
        "data": {
            "anchors": model_data(matched_anchors),
            "entity_profiles": model_data(action_entity_profile_views),
            "npc_profiles": model_data(action_npc_director_views),
        },
    }

    action_gateway = create_gateway()
    action_gateway.set_trace_context(turn_index=turn_index, session_type="generation")

    qa_events: Queue[dict] = Queue()

    def drain_qa_events():
        while True:
            try:
                yield qa_events.get_nowait()
            except Empty:
                break

    def emit(event: dict):
        yield event
        yield from drain_qa_events()

    def run_context_qa_branch() -> ContextAnswerSet:
        try:
            question_gateway = create_gateway()
            question_gateway.set_trace_context(turn_index=turn_index, session_type="generation")
            answer_gateway = create_gateway()
            answer_gateway.set_trace_context(turn_index=turn_index, session_type="generation")

            context_questions = generate_context_questions_workflow(
                ContextQuestionInput(
                    user_input=user_input,
                    current_state=current_state,
                    core_memory=core_memory,
                    entity_profiles=action_entity_profile_views,
                    npc_profiles=action_npc_director_views,
                ),
                question_gateway,
                trace_id=trace_id,
            )

            qa_events.put({
                "step": "context_questions_done",
                "data": {
                    "context_questions": model_data(context_questions),
                },
            })

            with memory_store() as mem_store:
                context_answers, context_answer_memories = answer_context_questions_workflow(
                    questions=context_questions,
                    core_memory=core_memory,
                    current_state=current_state,
                    director_event=active_director_event,
                    recent_archived_director_event=recent_archived_director_event,
                    scene_summaries=scene_summaries,
                    memory_store=mem_store,
                    gateway=answer_gateway,
                    trace_id=trace_id,
                )

            qa_events.put({
                "step": "context_answers_done",
                "data": {
                    "context_answers": model_data(context_answers),
                    "context_answer_memories": model_data(context_answer_memories),
                },
            })
            return context_answers
        except Exception as exc:
            qa_events.put({
                "step": "context_qa_failed",
                "data": {
                    "error": str(exc),
                    "trace_id": trace_id,
                },
            })
            return ContextAnswerSet(turn_focus="Context QA failed", answers=[])

    qa_executor = ThreadPoolExecutor(max_workers=1)
    qa_future = qa_executor.submit(run_context_qa_branch)

    parser = ActionParser(action_gateway)
    action_result = parser.extract_actions(
        user_input=user_input,
        current_state=current_state,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        entity_profiles=action_entity_profile_views,
        npc_profiles=action_npc_director_views,
        trace_id=trace_id,
    )
    raw_action_parse_result = ActionParseResult(
        analysis=action_result.analysis,
        turn_intent=action_result.turn_intent,
        atomic_actions=action_result.atomic_actions,
        anchors=matched_anchors,
        ambiguities=[],
    )
    action_self_check = ActionSelfCheck()
    action_parse_result = action_self_check.check(
        raw_action_parse_result,
        user_input,
        current_state,
    )
    action_self_check_result = action_self_check.last_result

    yield from emit({
        "step": "action_parsed",
        "data": {
            "raw_action_parse_result": model_data(raw_action_parse_result),
            "action_self_check_result": model_data(action_self_check_result),
            "action_parse_result": model_data(action_parse_result),
        },
    })

    with memory_store() as mem_store:
        retrieved_memories = retrieve_candidates_workflow(
            action_parse_result,
            mem_store,
        )

    yield from emit({
        "step": "retrieval_done",
        "data": {
            "retrieved_memories": model_data(retrieved_memories),
        },
    })

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

    yield from emit({
        "step": "modifiers_done",
        "data": {
            "roll_actions": model_data(roll_actions),
            "modifiers": model_data(modifier_batch),
        },
    })

    roll_results = resolve_rolls_workflow(
        action_parse_result,
        config=roll_config,
        modifier_batch=modifier_batch,
        roll_actions=roll_actions,
    )

    yield from emit({
        "step": "rolls_done",
        "data": {
            "roll_results": model_data(roll_results),
        },
    })

    context_answers = qa_future.result()
    yield from drain_qa_events()

    turn_resolution = resolve_turn_workflow(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        roll_results=roll_results,
        core_memory=core_memory,
        context_answers=context_answers,
        entity_profiles=action_entity_profile_views,
        npc_profiles=action_npc_director_views,
        director_event=active_director_event,
        recent_archived_director_event=recent_archived_director_event,
        gateway=gateway,
        trace_id=trace_id,
    )

    yield from emit({
        "step": "turn_resolution_done",
        "data": {
            "turn_resolution": model_data(turn_resolution),
        },
    })

    director_npc_result = None
    with director_npc_store() as npc_store, npc_profile_store() as profile_store:
        creator_gateway = create_gateway()
        creator_gateway.set_trace_context(turn_index=turn_index, session_type="generation")
        dispatcher_gateway = create_gateway()
        dispatcher_gateway.set_trace_context(turn_index=turn_index, session_type="generation")
        director_npc_result = maybe_director_npc_workflow(
            core_memory=core_memory,
            current_state=current_state,
            recent_summaries=recent_summaries,
            director_event=active_director_event,
            user_input=user_input,
            turn_index=turn_index,
            director_npc_store=npc_store,
            npc_profile_store=profile_store,
            creator_gateway=creator_gateway,
            dispatcher_gateway=dispatcher_gateway,
            trace_id=trace_id,
        )

    yield from emit({
        "step": "director_npc_done",
        "data": {
            "director_npc_result": model_data(director_npc_result),
        },
    })

    context = assemble_context(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        turn_resolution=turn_resolution,
        core_memory=core_memory,
        context_answers=context_answers,
        entity_profiles=action_entity_profile_views,
        npc_profiles=action_npc_director_views,
        director_event=active_director_event,
        recent_archived_director_event=recent_archived_director_event,
        director_npc_injection=(
            director_npc_result.plot_injection
            if director_npc_result is not None
            else None
        ),
    )

    yield from emit({
        "step": "context_assembled",
        "data": {
            "context": model_data(context),
        },
    })

    outline = generate_plot_outline_workflow(
        context=context,
        gateway=gateway,
        trace_id=trace_id,
    )

    yield from emit({
        "step": "outline_done",
        "data": {
            "plot_outline": model_data(outline),
        },
    })

    beat_entity_anchors = build_entity_retrieval_anchors(outline)
    with entity_store() as ent_store, npc_profile_store() as npc_store:
        writer_entity_profiles = gather_entity_profiles_from_state_and_anchors(
            current_state=current_state,
            anchors=[*action_parse_result.anchors, *beat_entity_anchors],
            entity_store=ent_store,
        )
        writer_npc_profiles = gather_npc_profiles_from_state_and_anchors(
            current_state=current_state,
            anchors=[*action_parse_result.anchors, *beat_entity_anchors],
            npc_store=npc_store,
        )
    writer_entity_profile_views = [
        profile.semantic_view()
        for profile in writer_entity_profiles
    ]
    writer_npc_public_views = [
        profile.public_view()
        for profile in writer_npc_profiles
    ]
    recent_scene = (
        short_term_memory.recent_scenes[-1]
        if short_term_memory.recent_scenes
        else None
    )
    next_turn_index = (recent_scene.turn_index + 1) if recent_scene else 1

    scene_result = generate_scene_workflow(
        outline=outline,
        current_state=current_state,
        core_memory=core_memory,
        context_answers=context_answers,
        short_term_memory=short_term_memory,
        gateway=gateway,
        turn_index=next_turn_index,
        entity_profiles=writer_entity_profile_views,
        npc_profiles=writer_npc_public_views,
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
        director_npc_result=director_npc_result,
        turn_index=next_turn_index,
        short_term_memory=short_term_memory,
    )

    qa_executor.shutdown(wait=False, cancel_futures=False)

    yield from emit({
        "step": "scene_written",
        "data": {
            "turn_id": turn_id,
            "trace_id": trace_id,
            "turn_index": next_turn_index,
            "writer_entity_profiles": model_data(writer_entity_profile_views),
            "writer_npc_profiles": model_data(writer_npc_public_views),
            "recent_scene": model_data(recent_scene),
            "scene_result": model_data(scene_result),
            "context": model_data(context),
            "status": "awaiting_confirm",
        },
    })
