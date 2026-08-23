from __future__ import annotations

import hashlib
import json

from app.core.diagnostics import diagnostics_from_state as _diagnostics
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.text import normalize_compact_key
from app.core.time import utc_now
from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.confirmed_history import build_turn_documents
from app.domain.continuity import ContinuityProjector
from app.domain.current_state import StateChangeSet, StateUpdater
from app.domain.director_event.reviewer import (
    DirectorEventEngagedReviewer,
    DirectorEventHiddenReviewer,
)
from app.domain.director_event.workflows import (
    review_engaged_events_workflow,
    review_hidden_events_workflow,
)
from app.domain.npc_profile.batch_updater import apply_npc_batch_update
from app.domain.npc_profile.identity_resolver import NpcCandidate
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.workflows import (
    prune_npc_transient_aliases_from_current_state,
)
from app.domain.scene_facts import (
    SceneFactExtractInput,
    SceneFactExtractor,
    SceneParagraphInput,
)
from app.domain.scene_summary.summarizer import SceneSummarizer
from app.domain.views import project_scene_facts
from app.domain.world_setting.schemas import setting_appears_in_text
from app.runtime.serialization import model_data

from .confirmation_cache import ConfirmationCache
from .confirmation_helpers import merge_unique_ids
from .confirmation_state import ConfirmationState
from .npc_result import NpcCandidateContext, NpcTurnNodeResult
from app.domain.director.confirmation import DirectorConfirmationExecutor



def _candidate_npc_id(
    *,
    scene_id: str,
    turn_index: int,
    primary_surface: str,
    occupied_ids: set[str],
) -> str:
    """候选正式 id：scene_id + turn_index + 规范化主称呼的稳定 hash。

    enrich 后该 id 会成为正式档案 id（非 ``draft:`` 占位）；冲突时扩展 hash 输入。
    """
    base = f"{scene_id}:{turn_index}:{normalize_compact_key(primary_surface)}"
    npc_id = f"cand_{hashlib.sha256(base.encode('utf-8')).hexdigest()[:16]}"
    while npc_id in occupied_ids:
        npc_id = f"cand_{hashlib.sha256(f'{base}:{npc_id}'.encode('utf-8')).hexdigest()[:16]}"
    return npc_id


def _candidate_shells(
    candidates: list[NpcCandidate],
    *,
    scene_id: str,
    turn_index: int,
    admitted_at: str,
    existing_profile_ids: set[str],
    director_draft_ids: set[str],
) -> tuple[list[NPCProfile], dict[str, NpcCandidateContext]]:
    occupied = set(existing_profile_ids) | set(director_draft_ids)
    shells: list[NPCProfile] = []
    context: dict[str, NpcCandidateContext] = {}
    for candidate in candidates:
        npc_id = _candidate_npc_id(
            scene_id=scene_id,
            turn_index=turn_index,
            primary_surface=candidate.primary_surface,
            occupied_ids=occupied,
        )
        occupied.add(npc_id)
        shells.append(NPCProfile(
            npc_id=npc_id,
            name=candidate.primary_surface,
            source_scene_ids=[scene_id],
            created_turn=turn_index,
            introduced_turn=turn_index,
            updated_at=admitted_at,
        ))
        context[npc_id] = NpcCandidateContext(
            primary_surface=candidate.primary_surface,
            presence=candidate.presence,
            alias_surfaces=[
                relation.surface for relation in candidate.alias_relations
            ],
            evidence_fact_ids=list(candidate.evidence_fact_ids),
            evidence_paragraph_ids=list(candidate.evidence_paragraph_ids),
        )
    return shells, context


def _gateway_for(state: ConfirmationState):
    pipeline_input = state["pipeline_input"]
    return state["runtime_services"].gateway(
        turn_index=pipeline_input.turn_index,
        session_type="confirmation",
    )


def _store_factory(state: ConfirmationState, name: str):
    value = getattr(state["runtime_services"].stores, name)
    return value() if callable(value) else value


def continuity_project_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    cache = ConfirmationCache(state["cache"])
    documents = build_turn_documents(
        scene_id=state["updated_state"].scene.scene_id,
        turn_index=pipeline_input.turn_index,
        user_input=pipeline_input.user_input,
        scene_text=state["effective_scene_text"],
    )
    with _store_factory(state, "continuity") as continuity_store:
        active_items = (
            []
            if pipeline_input.clear_continuity_before_save
            else continuity_store.list_active()
        )
        active_count = len(active_items)
    computed = False
    projector: ContinuityProjector | None = None

    def compute_projection():
        nonlocal computed, projector
        computed = True
        projector = ContinuityProjector(_gateway_for(state))
        return projector.project(
            current_documents=documents,
            active_items=active_items,
            trace_id=state["trace_id"],
        )

    projection = cache.compute_if_missing(
        "continuity_projection",
        compute_projection,
    )
    diagnostics = _diagnostics(state)
    if computed and projector is not None:
        for warning in getattr(projector, "last_warnings", []):
            diagnostics.warning(
                source="continuity",
                code=warning.code,
                message=warning.message,
                trace_id=state["trace_id"],
                task="continuity_project",
                turn_index=getattr(pipeline_input, "turn_index", None),
            )
    return {
        "continuity_projection": projection,
        "events": [{
            "step": "continuity_projected",
            "data": {
                "active_count": active_count,
                "continuity_projection": model_data(projection),
            },
        }],
    }


def _extract_staged_npc_materials(pending) -> list[dict[str, Any]]:
    stage = getattr(pending, "director_stage", None)
    if stage is None or not getattr(stage, "materials", None):
        return []
    staged: list[dict[str, Any]] = []
    for mat in stage.materials:
        if mat.kind != "npc":
            continue
        payload = mat.payload or {}
        source = payload.get("source")
        if source not in {"director_npc", "spontaneous"}:
            continue
        name = payload.get("name")
        if not name:
            continue
        staged.append({
            "name": name,
            "role": payload.get("role") or "",
            "appearance": payload.get("appearance") or "",
            "personality_trait": payload.get("personality_trait") or "",
            "guidance": mat.guidance or "",
            "source": source,
        })
    return staged


def scene_fact_extract_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = ConfirmationCache(state["cache"])
    core_memory = state["core_memory"]
    pending = state.get("pending") or getattr(pipeline_input, "pending", None)
    staged_npcs = _extract_staged_npc_materials(pending)
    scene_paragraphs = [
        SceneParagraphInput(
            document_id=document.document_id,
            text=document.text,
        )
        for document in build_turn_documents(
            scene_id=state["state_before_update"].scene.scene_id,
            turn_index=pipeline_input.turn_index,
            user_input=pipeline_input.user_input,
            scene_text=state["effective_scene_text"],
        )
        if document.source_type == "scene_paragraph"
    ]

    computed = False

    def compute_scene_facts():
        nonlocal computed
        computed = True
        return SceneFactExtractor(_gateway_for(state)).extract(
            SceneFactExtractInput(
                scene_text=state["effective_scene_text"],
                current_state=state["state_before_update"],
                core_memory=core_memory,
                scene_paragraphs=scene_paragraphs,
                staged_npcs=staged_npcs,
            ),
            trace_id=trace_id,
        )

    scene_facts = cache.compute_if_missing("scene_facts", compute_scene_facts)
    diagnostics = _diagnostics(state)
    if computed and scene_facts.degraded:
        reasons = set(scene_facts.degraded_reasons)
        if reasons & {"facts", "provider", "parse", "structure"} or not reasons:
            diagnostics.warning(
                source="scene_facts",
                code="scene_facts_degraded",
                message="Scene Facts 降级：facts 或整体输出不可用。",
                trace_id=trace_id,
                task="scene_facts",
                turn_index=getattr(pipeline_input, "turn_index", None),
            )
        if "npc_mentions" in reasons:
            diagnostics.warning(
                source="scene_facts",
                code="npc_mentions_degraded",
                message="Scene Facts 降级：npc_mentions 不可用或部分被清理。",
                trace_id=trace_id,
                task="scene_facts",
                turn_index=getattr(pipeline_input, "turn_index", None),
            )
    return {
        "scene_facts": scene_facts,
        "scene_facts_json": project_scene_facts(scene_facts),
        "events": [{
            "step": "scene_facts_done",
            "data": {
                "scene_facts": model_data(scene_facts),
            },
        }],
    }


def state_update_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = ConfirmationCache(state["cache"])
    computed = False

    def compute_update():
        nonlocal computed
        computed = True
        gateway = _gateway_for(state)
        updater = StateUpdater(gateway)
        npc_aliases = {
            profile.name: [
                *profile.aliases,
                *list(profile.transient_aliases.keys()),
            ]
            for profile in state["existing_npc_profiles"]
            if profile.name
            and (profile.aliases or profile.transient_aliases)
        }
        paragraph_texts: dict[str, str] = {}
        scene_facts = state.get("scene_facts")
        if scene_facts is not None and not scene_facts.facts:
            paragraph_texts = {
                document.document_id: document.text
                for document in build_turn_documents(
                    scene_id=state["state_before_update"].scene.scene_id,
                    turn_index=pipeline_input.turn_index,
                    user_input=pipeline_input.user_input,
                    scene_text=state["effective_scene_text"],
                )
                if document.source_type == "scene_paragraph"
            }
        updated_state = updater.update(
            state["state_before_update"],
            state["scene_facts_json"],
            core_memory=state["core_memory"],
            npc_aliases=npc_aliases,
            paragraph_texts=paragraph_texts,
            trace_id=trace_id,
        )
        state_change_set = getattr(
            updater,
            "last_change_set",
            StateChangeSet(decision="State update produced no change set."),
        )
        state_update_warnings = list(getattr(updater, "last_warnings", []))
        return updated_state, state_change_set, state_update_warnings

    updated_state, state_change_set, state_update_warnings = cache.atomic(
        "updated_state",
        "state_change_set",
        "state_update_warnings",
        factory=compute_update,
    )
    diagnostics = _diagnostics(state)
    if computed:
        for warning in state_update_warnings:
            warning_data = model_data(warning)
            diagnostics.warning(
                source="state_update",
                code=warning.code,
                message=warning.message,
                trace_id=trace_id,
                task="state_update",
                turn_index=getattr(pipeline_input, "turn_index", None),
                details=warning_data,
            )
    return {
        "updated_state": updated_state,
        "state_change_set": state_change_set,
        "state_update_warnings": state_update_warnings,
        "events": [{
            "step": "state_update_done",
            "data": {
                "state_after": model_data(updated_state),
                "state_changes": model_data(state_change_set),
                "warnings": model_data(state_update_warnings),
            },
        }],
    }


def scene_summarize_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    cache = ConfirmationCache(state["cache"])
    computed = False
    summarizer: SceneSummarizer | None = None

    def compute_turn_summary():
        nonlocal computed, summarizer
        computed = True
        summarizer = SceneSummarizer(_gateway_for(state))
        return summarizer.summarize_turn(
            scene_text=state["confirmation_scene_text"],
            current_state=state["updated_state"],
            core_memory=state["core_memory"],
            scene_id=state["updated_state"].scene.scene_id,
            turn_index=pipeline_input.turn_index,
            trace_id=state["trace_id"],
        )

    turn_summary = cache.compute_if_missing("turn_summary", compute_turn_summary)
    diagnostics = _diagnostics(state)
    if computed and summarizer is not None:
        for warning in getattr(summarizer, "last_warnings", []):
            diagnostics.warning(
                source="scene_summary",
                code=warning.code,
                message=warning.message,
                trace_id=state["trace_id"],
                task="scene_summarize",
                turn_index=getattr(pipeline_input, "turn_index", None),
            )
    return {
        "turn_summary": turn_summary,
        "events": [{
            "step": "scene_summarized",
            "data": {"turn_summary": model_data(turn_summary)},
        }],
    }


def action_preview_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    cache = ConfirmationCache(state["cache"])
    computed = False
    agent: ActionPreviewAgent | None = None

    def compute_action_preview():
        nonlocal computed, agent
        scene_was_edited = (
            state["effective_scene_text"]
            != pipeline_input.scene_result.scene_text
        )
        computed = True
        agent = ActionPreviewAgent(_gateway_for(state))
        return agent.generate(
            current_state=state["updated_state"],
            core_memory=state["core_memory"],
            recent_summaries=state.get("recent_summaries", []),
            next_action_directions=(
                []
                if scene_was_edited
                else pipeline_input.scene_result.next_action_directions
            ),
            scene_facts=state["scene_facts"],
            scene_text=(
                state["effective_scene_text"]
                if not state["scene_facts"].facts
                else None
            ),
            trace_id=trace_id,
        )

    action_preview = cache.compute_if_missing("action_preview", compute_action_preview)
    diagnostics = _diagnostics(state)
    if computed and agent is not None:
        for warning in getattr(agent, "last_warnings", []):
            diagnostics.warning(
                source="action_preview",
                code=warning.code,
                message=warning.message,
                trace_id=trace_id,
                task="action_preview",
                turn_index=getattr(pipeline_input, "turn_index", None),
            )
    return {
        "action_preview": action_preview,
        "events": [{
            "step": "action_preview_done",
            "data": {
                "action_preview": model_data(action_preview),
            },
        }],
    }


def director_agent_confirm_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    if not state.get("write_db", True):
        cache = ConfirmationCache(state["cache"])
        return {
            "director_decision": cache.result("director_decision"),
            "director_executions": cache.result("director_executions"),
        }

    cache = ConfirmationCache(state["cache"])
    computed = False
    executor: DirectorConfirmationExecutor | None = None

    def compute_decision():
        nonlocal computed, executor
        computed = True
        executor = DirectorConfirmationExecutor(
            gateway=_gateway_for(state),
            stores=state["runtime_services"].stores,
            core_memory=state["core_memory"],
            current_state=state["updated_state"],
            user_input=pipeline_input.user_input,
            scene_facts=state["scene_facts"].model_dump(mode="json"),
            recent_summaries=state.get("recent_summaries", []),
            selection_summaries=state.get("selection_summaries", []),
            turn_index=pipeline_input.turn_index,
            trace_id=state["trace_id"],
            new_npc_profiles=state.get("new_npc_profiles", []),
            npc_candidate_context=_candidate_context_plain(
                state.get("npc_candidate_context", {})
            ),
            scene_text=state["effective_scene_text"],
            director_log_entry=getattr(
                state.get("pending"), "director_log_entry", None
            ),
        )
        return executor.run()

    decision, executions = cache.atomic(
        "director_decision",
        "director_executions",
        factory=compute_decision,
    )
    diagnostics = _diagnostics(state)
    if computed and executor is not None:
        for warning in getattr(executor, "last_warnings", []):
            diagnostics.warning(
                source="director",
                code=warning.code,
                message=warning.message,
                trace_id=state["trace_id"],
                task="director_agent_confirm",
                turn_index=getattr(pipeline_input, "turn_index", None),
            )
    return {
        "director_decision": decision,
        "director_executions": executions,
        "events": [{
            "step": "director_agent_confirmed",
            "data": {
                "director_decision": model_data(decision),
                "director_executions": model_data(executions),
            },
        }],
    }


def npc_profile_update_node(state: ConfirmationState) -> ConfirmationState:
    cache = ConfirmationCache(state["cache"])
    if not state.get("write_db", True):
        return {
            "updated_npc_profile_ids": [],
            "new_npc_profiles": [],
            "npc_candidate_context": {},
        }
    pipeline_input = state["pipeline_input"]
    trace_id = state["trace_id"]
    computed = False

    def compute_npc_update():
        nonlocal computed
        computed = True
        with _store_factory(state, "npc_profile") as npc_store:
            gateway = _gateway_for(state)
            admitted_at = utc_now()
            evidence_documents = {
                document.document_id: document
                for document in build_turn_documents(
                    scene_id=state["updated_state"].scene.scene_id,
                    turn_index=pipeline_input.turn_index,
                    user_input=pipeline_input.user_input,
                    scene_text=state["effective_scene_text"],
                )
                if document.source_type == "scene_paragraph"
            }
            result = apply_npc_batch_update(
                npc_store=npc_store,
                gateway=gateway,
                scene_facts=state["scene_facts"],
                evidence_documents=evidence_documents,
                current_state=state["updated_state"],
                turn_index=pipeline_input.turn_index,
                scene_id=state["updated_state"].scene.scene_id,
                admitted_at=admitted_at,
                trace_id=trace_id,
            )
            alias_pruned_ids = prune_npc_transient_aliases_from_current_state(
                npc_store=npc_store,
                turn_index=pipeline_input.turn_index,
            )
            shells, context = _candidate_shells(
                result.new_npc_candidates,
                scene_id=state["updated_state"].scene.scene_id,
                turn_index=pipeline_input.turn_index,
                admitted_at=admitted_at,
                existing_profile_ids={
                    profile.npc_id
                    for profile in state.get("existing_npc_profiles", [])
                },
                director_draft_ids=_director_draft_ids(state),
            )
            return NpcTurnNodeResult(
                process_result=result,
                updated_npc_profile_ids=merge_unique_ids(
                    [*result.saved_ids, *alias_pruned_ids]
                ),
                new_npc_profiles=shells,
                candidate_context=context,
            )

    node_result = cache.compute_if_missing("npc_turn_result", compute_npc_update)
    warnings = node_result.process_result.warnings
    warning_summary = [
        {
            "npc_name": warning.npc_name,
            "field": warning.field_name,
            "code": warning.code,
            "evidence_ids": list(warning.evidence_ids),
        }
        for warning in warnings
    ]
    diagnostics = _diagnostics(state)
    update_events = []
    if computed:
        for warning in warnings:
            diagnostics.warning(
                source="npc_batch_update",
                code=warning.code,
                message=warning.message,
                trace_id=trace_id,
                task="npc_batch_update",
                turn_index=getattr(pipeline_input, "turn_index", None),
                details={
                    "npc_name": warning.npc_name,
                    "field": warning.field_name,
                    "evidence_ids": list(warning.evidence_ids),
                },
            )
        update_events = [{
            "step": "npc_profile_updated",
            "data": {
                "updated_npc_profile_ids": node_result.updated_npc_profile_ids,
                "new_npc_candidates": [
                    {
                        "npc_id": npc_id,
                        "name": context.primary_surface,
                        "alias_surfaces": list(context.alias_surfaces),
                    }
                    for npc_id, context in sorted(
                        node_result.candidate_context.items()
                    )
                ],
                "warnings": warning_summary,
            },
        }]
    return {
        "updated_npc_profile_ids": node_result.updated_npc_profile_ids,
        "new_npc_profiles": node_result.new_npc_profiles,
        "npc_candidate_context": node_result.candidate_context,
        "npc_warnings": warning_summary,
        "events": update_events,
    }


def _director_draft_ids(state: ConfirmationState) -> set[str]:
    with _store_factory(state, "director_npc") as director_npc_store:
        return {draft.draft_id for draft in director_npc_store.list_all()}


def _candidate_context_plain(
    context: dict[str, NpcCandidateContext],
) -> dict[str, dict]:
    """把图侧候选上下文转成纯 dict，避免 domain 依赖 graph 类型。"""
    return {
        npc_id: {
            "primary_surface": candidate.primary_surface,
            "presence": candidate.presence,
            "alias_surfaces": list(candidate.alias_surfaces),
            "evidence_fact_ids": list(candidate.evidence_fact_ids),
            "evidence_paragraph_ids": list(candidate.evidence_paragraph_ids),
        }
        for npc_id, candidate in context.items()
    }


def setting_appearance_update_node(state: ConfirmationState) -> ConfirmationState:
    """Count once how many world settings appear in the confirmed scene text.

    Mirrors npc_profile_update_node: writes directly to the store, keeps its
    result in the write-scoped confirmation cache so re-entry never double
    counts, and rolls back with the confirmation as a whole on failure.
    """
    if not state.get("write_db", True):
        return {"recorded_setting_ids": []}
    cache = ConfirmationCache(state["cache"])
    if cache.produced("recorded_setting_ids"):
        return {"recorded_setting_ids": cache.result("recorded_setting_ids")}
    folded_text = state["effective_scene_text"].casefold()
    recorded_ids: list[str] = []
    if folded_text:
        with _store_factory(state, "world_setting") as setting_store:
            for setting in setting_store.list_all():
                if setting_appears_in_text(setting.name, folded_text):
                    if setting_store.record_mention(setting.setting_id):
                        recorded_ids.append(setting.setting_id)
    return {
        "recorded_setting_ids": recorded_ids,
        "events": [{
            "step": "setting_appearances_recorded",
            "data": {"count": len(recorded_ids)},
        }],
    }


def director_event_review_node(state: ConfirmationState) -> ConfirmationState:
    pipeline_input = state["pipeline_input"]
    if not state.get("write_db", True):
        return {
            "director_event_reviews": ConfirmationCache(
                state["cache"]
            ).result("director_event_reviews")
            or [],
        }

    cache = ConfirmationCache(state["cache"])
    if cache.produced("director_event_reviews"):
        return {"director_event_reviews": cache.result("director_event_reviews")}

    gateway = _gateway_for(state)
    turn_index = pipeline_input.turn_index
    reviews = []
    review_warnings: list[tuple[str, str]] = []
    scene_facts_json = json.dumps(
        state["scene_facts"].model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    with _store_factory(state, "director_event") as event_store:
        active_events = [
            event
            for event in event_store.list_active()
            if event.created_turn < turn_index
        ]
        hidden_events = [event for event in active_events if event.stage == "hidden"]
        engaged_events = [
            event for event in active_events if event.stage == "engaged"
        ]
        if hidden_events:
            try:
                hidden_results = review_hidden_events_workflow(
                    active_events=hidden_events,
                    user_input=pipeline_input.user_input,
                    scene_facts=scene_facts_json,
                    current_state=state["updated_state"],
                    core_memory=state["core_memory"],
                    scene_summaries=state.get("recent_summaries", []),
                    turn_index=turn_index,
                    store=event_store,
                    reviewer=DirectorEventHiddenReviewer(gateway),
                    trace_id=state["trace_id"],
                )
            except (
                LLMOutputParseError,
                LLMOutputValidationError,
            ) as exc:
                review_warnings.append((
                    "director_event_review_fallback",
                    f"Hidden event review failed; skipped: {exc}",
                ))
            else:
                reviews.extend(
                    result.model_dump(mode="json")
                    for result in hidden_results
                )
                engaged_ids = {event.event_id for event in engaged_events}
                for result in hidden_results:
                    event = result.active_after
                    if (
                        event is not None
                        and event.stage == "engaged"
                        and state["updated_state"].world_clock.has_reached(
                            event.deadline_clock
                        )
                        and event.event_id not in engaged_ids
                    ):
                        engaged_events.append(event)
                        engaged_ids.add(event.event_id)
        if engaged_events:
            try:
                engaged_results = review_engaged_events_workflow(
                    active_events=engaged_events,
                    user_input=pipeline_input.user_input,
                    scene_facts=scene_facts_json,
                    current_state=state["updated_state"],
                    core_memory=state["core_memory"],
                    scene_summaries=state.get("recent_summaries", []),
                    turn_index=turn_index,
                    store=event_store,
                    reviewer=DirectorEventEngagedReviewer(gateway),
                    trace_id=state["trace_id"],
                )
            except (
                LLMOutputParseError,
                LLMOutputValidationError,
            ) as exc:
                review_warnings.append((
                    "director_event_review_fallback",
                    f"Engaged event review failed; skipped: {exc}",
                ))
            else:
                reviews.extend(
                    result.model_dump(mode="json")
                    for result in engaged_results
                )

    diagnostics = _diagnostics(state)
    for code, message in review_warnings:
        diagnostics.warning(
            source="director_event",
            code=code,
            message=message,
            trace_id=state["trace_id"],
            task="director_event_review",
            turn_index=getattr(pipeline_input, "turn_index", None),
        )
    return {
        "director_event_reviews": reviews,
        "events": [{
            "step": "director_event_reviewed",
            "data": {
                "director_event_reviews": reviews,
            },
        }],
    }
