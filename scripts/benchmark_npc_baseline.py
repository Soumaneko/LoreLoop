"""Replayable baseline benchmark for the NPC extract + update pipeline.

This benchmark measures the CURRENT two-LLM-call NPC pipeline
(``npc_profile_extract`` + ``npc_profile_update``) before the NPCTurnProcessor
refactor, and later the single-call processor. The design separates the
input/gold contract from the recorded LLM outputs so the benchmark can compare
across refactors:

- ``fixtures.json`` is the input + gold contract. It never contains recorded
  LLM output. Its SHA-256 is the single fingerprint that gates comparison: any
  change requires explicit baseline review.
- ``recordings/`` holds recorded LLM outputs, one versioned file per backend
  (``legacy.json`` for the two old tasks). Recording changes (a real
  ``--record`` run, or a new processor's schema) do NOT invalidate the
  contract; they only change the report, which is then diffed and re-accepted.
- Both backends project their recorded outputs through the same deterministic
  pipeline into the SAME normalized ``signals`` shape and score against the
  SAME gold, so legacy vs NPCTurnProcessor quality is comparable.

Execution modes:

- default / ``--check``: fully offline and byte-deterministic. Recorded
  outputs from ``recordings/legacy.json`` are replayed through a stub gateway,
  the real deterministic pipeline runs, and the report is compared against the
  accepted ``baseline.json`` with a regression gate.
- ``--record``: runs the real two-task pipeline against a configured LLM
  gateway, writes raw outputs and ``token_usage`` into the legacy recordings
  file. Requires ``OPENAI_API_KEY`` (or a compatible base URL) and is meant to
  be run by a developer with API access. Refuses to overwrite an existing
  recordings file without ``--overwrite`` and uses a per-run trace id so
  re-runs never read stale traces.

No judge LLM is used: gold expectations are scored directly against the
recorded outputs and the deterministic downstream results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.text import normalize_key
from app.domain.confirmed_history import build_turn_documents
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import (
    CurrentState,
    ParticipantState,
    PlayerState,
    SceneState,
)
from app.domain.npc_profile.schemas import KnownAbility, NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.updater import _existing_option_for_prompt
from app.domain.npc_profile.workflows import update_npc_profiles_workflow
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.views import project_current_state, project_scene_summaries


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "benchmarks" / "npc_baseline" / "fixtures.json"
DEFAULT_RECORDINGS = ROOT / "benchmarks" / "npc_baseline" / "recordings" / "legacy.json"
DEFAULT_BASELINE = ROOT / "benchmarks" / "npc_baseline" / "baseline.json"
BENCHMARK_VERSION = 2

#: Updater-modifiable profile fields, in a stable order for diff projection.
UPDATER_FIELDS = (
    "last_known_location",
    "attitude_to_player",
    "current_intent",
    "public_summary",
    "role",
    "appearance",
    "personality_trait",
    "faction",
    "known_abilities",
)

IMPORTANCE = Literal["must", "should"]
BACKEND = Literal["legacy", "npcturn"]

#: Per-backend NPC gateway call-count contract. ``call_count`` is a backend
#: policy, not a shared gold expectation: the legacy pipeline always makes the
#: extract call and the update call only when extraction found mentions (so 1
#: or 2 calls), while the NPCTurnProcessor must make at most 1.
CALL_COUNT_BOUNDS: dict[BACKEND, tuple[int, int]] = {
    "legacy": (1, 2),
    "npcturn": (0, 1),
}


class AbilityFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""


class RosterProfileFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    transient_aliases: dict[str, int] = Field(default_factory=dict)
    public_summary: str | None = None
    role: str | None = None
    appearance: str | None = None
    personality_trait: str | None = None
    faction: str | None = None
    known_abilities: list[AbilityFixture] = Field(default_factory=list)
    last_known_location: str | None = None
    attitude_to_player: str | None = None
    current_intent: str | None = None
    source_scene_ids: list[str] = Field(default_factory=list)
    #: Explicit ordering key. ``NPCProfileStore.list_all()`` orders by
    #: ``updated_at DESC, name ASC``; fixtures pin the value so the candidate
    #: pool order (and thus candidate_index) is byte-deterministic.
    updated_at: str = ""

    def to_profile(self) -> NPCProfile:
        return NPCProfile(
            npc_id=self.npc_id,
            name=self.name,
            aliases=list(self.aliases),
            transient_aliases=dict(self.transient_aliases),
            public_summary=self.public_summary,
            role=self.role,
            appearance=self.appearance,
            personality_trait=self.personality_trait,
            known_abilities=[
                KnownAbility(name=ability.name, description=ability.description)
                for ability in self.known_abilities
            ],
            faction=self.faction,
            last_known_location=self.last_known_location,
            attitude_to_player=self.attitude_to_player,
            current_intent=self.current_intent,
            source_scene_ids=list(self.source_scene_ids),
            updated_at=self.updated_at,
        )


class CoreMemoryItemFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal[
        "world_invariant",
        "protagonist_invariant",
        "narrative_preference",
        "system_convention",
        "ability_rule",
    ]
    strength: Literal["hard_constraint", "strong_preference"]
    source: Literal["player", "campaign_setup"]
    content: str = Field(min_length=1)


class MentionExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    importance: IMPORTANCE = "must"


class ExactExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    importance: IMPORTANCE = "must"


class ResolutionExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_name: str = Field(min_length=1)
    resolution: Literal["existing", "alias_match", "new", "ignore"]
    importance: IMPORTANCE = "must"
    #: Required alias-match target when set: the recorded proposal must resolve
    #: this candidate to exactly this npc_id.
    target_npc_id: str | None = None


class ForbiddenResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_name: str = Field(min_length=1)
    resolution: Literal["existing", "alias_match", "new", "ignore"]


class AliasExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    importance: IMPORTANCE = "must"


class ForbiddenAlias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    surface: str = Field(min_length=1)


class StableFieldExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    value: Any = None
    importance: IMPORTANCE = "must"


class ForbiddenFieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    field: str = Field(min_length=1)


class NewNpcExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    importance: IMPORTANCE = "must"


class WarningExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    code: str = Field(min_length=1)
    importance: IMPORTANCE = "must"


class NoOpExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    importance: IMPORTANCE = "must"


class CaseGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention_recall: list[MentionExpectation] = Field(default_factory=list)
    forbidden_mentions: list[str] = Field(default_factory=list)
    exact_expectations: list[ExactExpectation] = Field(default_factory=list)
    resolution_expectations: list[ResolutionExpectation] = Field(default_factory=list)
    forbidden_resolutions: list[ForbiddenResolution] = Field(default_factory=list)
    alias_expectations: list[AliasExpectation] = Field(default_factory=list)
    forbidden_aliases: list[ForbiddenAlias] = Field(default_factory=list)
    stable_field_expectations: list[StableFieldExpectation] = Field(
        default_factory=list
    )
    forbidden_field_changes: list[ForbiddenFieldChange] = Field(default_factory=list)
    new_npc_expectations: list[NewNpcExpectation] = Field(default_factory=list)
    forbidden_new_names: list[str] = Field(default_factory=list)
    warning_expectations: list[WarningExpectation] = Field(default_factory=list)
    #: Must end with no updater-modifiable field delta (aliases, hit counts,
    #: ``source_scene_ids`` and timestamps are deliberately out of scope).
    no_op_expectations: list[NoOpExpectation] = Field(default_factory=list)


class RecordedCase(BaseModel):
    """Recorded LLM outputs for one case under a given backend.

    ``npc_profile_extract`` and ``npc_profile_update`` are the raw parsed
    outputs of the two gateway calls (legacy backend). ``npc_profile_update``
    is null when the updater legitimately short-circuits (empty extraction),
    in which case only the extract call happened. ``token_usage`` carries the
    provider usage dict per task, summed over ALL provider attempts (retries
    included) so the reported cost is the real cost. ``attempts`` counts the
    provider attempts per task; a retry makes ``attempts`` larger than the
    logical call count.
    """

    model_config = ConfigDict(extra="forbid")

    npc_profile_extract: dict[str, Any]
    npc_profile_update: dict[str, Any] | None = None
    token_usage: dict[str, Any] | None = None
    attempts: dict[str, int] | None = None

    def provider_attempts(self) -> int:
        if not self.attempts:
            return 0
        return sum(self.attempts.values())


class RecordingsSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    backend: BACKEND
    cases: dict[str, RecordedCase] = Field(min_length=1)


class RosterTemplate(BaseModel):
    """Generate a large deterministic roster for option-cap cases."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1)
    name_prefix: str = Field(min_length=1)

    def build(self) -> list[RosterProfileFixture]:
        return [
            RosterProfileFixture(
                npc_id=f"{self.name_prefix}{index:03d}",
                name=f"{self.name_prefix}{index:03d}",
                updated_at=f"2026-08-12T00:00:{200 - index:03d}Z",
            )
            for index in range(self.count)
        ]


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    turn_index: int = Field(default=0, ge=0)
    user_input: str = ""
    scene_text: str = Field(min_length=1)
    #: The confirmed Scene Facts payload exactly as the pipeline consumes it:
    #: valid ``SceneFactSet`` JSON.
    scene_facts: str = Field(min_length=1)
    player_name: str = Field(min_length=1)
    scene_location: str = Field(min_length=1)
    participants: list[str] = Field(default_factory=list)
    core_memory_items: list[CoreMemoryItemFixture] = Field(default_factory=list)
    roster: list[RosterProfileFixture] = Field(default_factory=list)
    roster_template: RosterTemplate | None = None
    gold: CaseGold

    @model_validator(mode="after")
    def validate_roster_source(self):
        if self.roster and self.roster_template is not None:
            raise ValueError("case cannot set both roster and roster_template")
        return self

    @model_validator(mode="after")
    def validate_scene_facts_schema(self):
        SceneFactSet.model_validate_json(self.scene_facts)
        return self

    def effective_roster(self) -> list[RosterProfileFixture]:
        if self.roster_template is not None:
            return self.roster_template.build()
        return list(self.roster)


class BenchmarkSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    cases: list[BenchmarkCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self):
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("benchmark case IDs must be unique")
        return self


def load_fixtures(path: Path) -> BenchmarkSuite:
    return BenchmarkSuite.model_validate_json(path.read_text(encoding="utf-8"))


def load_recordings(path: Path) -> RecordingsSuite:
    return RecordingsSuite.model_validate_json(path.read_text(encoding="utf-8"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixtures_fingerprint(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _recordings_fingerprint(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _gateway_for_case(trace_path: Path) -> Any:
    from app.core.llm.config import create_gateway

    trace_path.parent.mkdir(parents=True, exist_ok=True)
    return create_gateway(log_path=str(trace_path))


def _trace_task_records(entries: list[dict]) -> dict[str, dict[str, Any]]:
    """Group trace entries by task, keeping every attempt.

    Returns ``task -> {"parsed_output", "token_usage", "attempts"}`` where
    ``parsed_output`` is the final schema-valid output, ``token_usage`` is the
    provider usage summed over all attempts (retries included), and
    ``attempts`` counts the provider calls for that task.
    """
    by_task: dict[str, list[dict]] = {}
    for entry in entries:
        task = entry.get("task")
        if not isinstance(task, str):
            continue
        by_task.setdefault(task, []).append(entry)
    records: dict[str, dict[str, Any]] = {}
    for task, task_entries in by_task.items():
        valid = [
            entry
            for entry in task_entries
            if entry.get("schema_valid") is True
            and entry.get("parsed_output") is not None
        ]
        if not valid:
            continue
        final = valid[-1]
        token_totals: dict[str, int] = {}
        for entry in task_entries:
            usage = entry.get("token_usage") or {}
            for key in ("prompt_tokens", "completion_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    token_totals[key] = token_totals.get(key, 0) + value
        records[task] = {
            "parsed_output": final["parsed_output"],
            "token_usage": dict(sorted(token_totals.items())) or None,
            "attempts": len(task_entries),
        }
    return records


def _trace_records_for(case_id: str, trace_id: str, gateway: Any) -> dict[str, Any]:
    entries = gateway.tracer.read_traces(trace_id)
    return _trace_task_records(entries)


def _normalize_field_value(value: Any) -> Any:
    if isinstance(value, list):
        def _ability(item: Any) -> dict:
            name = getattr(item, "name", None)
            description = getattr(item, "description", None)
            if name is None and isinstance(item, dict):
                name = item.get("name", "")
                description = item.get("description", "")
            return {
                "name": str(name or ""),
                "description": str(description or ""),
            }

        return sorted(
            [_ability(item) for item in value],
            key=lambda item: normalize_key(item["name"]),
        )
    return value


def _to_json_value(profile: NPCProfile, field: str) -> Any:
    value = getattr(profile, field)
    if field == "known_abilities":
        return _normalize_field_value(value)
    return value


def _project_field_deltas(
    store: NPCProfileStore,
    case: BenchmarkCase,
) -> dict[str, dict[str, Any]]:
    after = {profile.npc_id: profile for profile in store.list_all()}
    deltas: dict[str, dict[str, Any]] = {}
    for fixture in case.effective_roster():
        original = fixture.to_profile()
        profile = after.get(fixture.npc_id)
        if profile is None:
            continue
        changed: dict[str, Any] = {}
        for field in UPDATER_FIELDS:
            before = _normalize_field_value(getattr(original, field))
            after_value = _normalize_field_value(getattr(profile, field))
            if before != after_value:
                changed[field] = _to_json_value(profile, field)
        if changed:
            deltas[fixture.npc_id] = changed
    return deltas


def _aliases_added(store: NPCProfileStore, case: BenchmarkCase) -> dict[str, list[str]]:
    after = {profile.npc_id: profile for profile in store.list_all()}
    added: dict[str, list[str]] = {}
    for fixture in case.effective_roster():
        original_keys = {normalize_key(alias) for alias in fixture.aliases}
        profile = after.get(fixture.npc_id)
        if profile is None:
            continue
        new_aliases = [
            alias
            for alias in profile.aliases
            if normalize_key(alias) not in original_keys
        ]
        if new_aliases:
            added[fixture.npc_id] = new_aliases
    return added


def _build_candidate_view(case: BenchmarkCase, update_input: dict | None) -> list[dict]:
    if update_input is None:
        return []
    raw = update_input.get("npc_candidates_json")
    if not raw:
        return []
    projection = json.loads(raw)
    roster = [profile.npc_id for profile in case.effective_roster()]
    view: list[dict] = []
    for candidate in projection:
        index = int(candidate["candidate_index"])
        source = candidate["source"]
        if source == "existing":
            matched_npc_index = candidate.get("matched_npc_index")
            mention_names = list(candidate.get("mention_names") or [])
            name = mention_names[0] if mention_names else ""
            npc_id = (
                roster[matched_npc_index]
                if matched_npc_index is not None
                and 0 <= matched_npc_index < len(roster)
                else None
            )
            view.append({
                "index": index,
                "name": name,
                "source": source,
                "npc_id": npc_id,
                "mention_names": mention_names,
            })
        else:
            view.append({
                "index": index,
                "name": candidate["name"],
                "source": source,
                "npc_id": None,
            })
    return view


def _prompt_metrics(case: BenchmarkCase, calls: list[dict]) -> dict:
    extract_input: dict | None = None
    update_input: dict | None = None
    for call in calls:
        if call["task"] == "npc_profile_extract":
            extract_input = call["input_data"]
        elif call["task"] == "npc_profile_update":
            update_input = call["input_data"]

    extract_metrics: dict = {"scene_facts_chars": 0}
    if extract_input is not None:
        extract_metrics = {
            "scene_facts_chars": len(extract_input.get("scene_facts_json", "")),
        }

    content_keys = (
        "core_memory_json",
        "scene_facts_json",
        "current_state_summary_json",
        "recent_summaries_json",
        "scene_paragraph_evidence_json",
        "npc_candidates_json",
        "existing_npc_options_json",
    )
    update_metrics: dict = {
        "candidates": 0,
        "existing_options": 0,
        "roster_total": len(case.effective_roster()),
        "evidence_paragraphs": 0,
        "content_chars": 0,
    }
    if update_input is not None:
        content_chars = 0
        for key in content_keys:
            content_chars += len(update_input.get(key, ""))
        update_metrics = {
            "candidates": len(_json_list(update_input, "npc_candidates_json")),
            "existing_options": len(_json_list(update_input, "existing_npc_options_json")),
            "roster_total": len(case.effective_roster()),
            "evidence_paragraphs": len(
                _json_list(update_input, "scene_paragraph_evidence_json")
            ),
            "content_chars": content_chars,
        }

    return {
        "npc_profile_extract": extract_metrics,
        "npc_profile_update": update_metrics,
        "call_count": len(calls),
    }


def _json_list(input_data: dict, key: str) -> list:
    raw = input_data.get(key)
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


class StubGateway:
    """Replays recorded LLM outputs in call order, mirroring the test gateway."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> Any:
        self.calls.append({
            "task": kwargs["task"],
            "input_data": kwargs["input_data"],
            "output_schema": kwargs["output_schema"],
        })
        if not self._responses:
            raise RuntimeError(
                "stub gateway ran out of recorded responses for task "
                f"{kwargs['task']!r}"
            )
        response = self._responses.pop(0)
        return kwargs["output_schema"].model_validate(response)


def _run_offline_legacy(
    case: BenchmarkCase,
    recorded: RecordedCase,
) -> dict[str, Any]:
    """Replay recorded legacy outputs through the real deterministic pipeline."""
    evidence_documents = {
        document.document_id: document
        for document in build_turn_documents(
            scene_id=case.scene_id,
            turn_index=case.turn_index,
            user_input=case.user_input,
            scene_text=case.scene_text,
        )
    }
    responses = [recorded.npc_profile_extract]
    if recorded.npc_profile_update is not None:
        responses.append(recorded.npc_profile_update)
    with NPCProfileStore(":memory:") as store:
        for profile in case.effective_roster():
            store.upsert(profile.to_profile())
        gateway = StubGateway(responses)
        result = update_npc_profiles_workflow(
            scene_facts=case.scene_facts,
            current_state=_current_state(case),
            core_memory=_core_memory(case),
            gateway=gateway,  # type: ignore[arg-type]
            npc_store=store,
            evidence_documents=evidence_documents,
            turn_index=case.turn_index,
            recent_summaries=[],
            trace_id=f"bench_npc_{case.case_id}",
        )
        field_deltas = _project_field_deltas(store, case)
        aliases_added = _aliases_added(store, case)

    update_call = next(
        (call for call in gateway.calls if call["task"] == "npc_profile_update"),
        None,
    )
    candidates = _build_candidate_view(case, update_call and update_call["input_data"])
    resolutions, proposal_invariants = _legacy_resolution_signals(
        case,
        recorded,
        candidates,
    )
    prompt_metrics = _prompt_metrics(case, gateway.calls)
    return {
        "call_count": len(gateway.calls),
        "candidates": candidates,
        "resolutions": resolutions,
        "proposal_invariants": proposal_invariants,
        #: Backend-neutral prompt size: total content chars across all NPC
        #: gateway calls in this turn (extract + update for legacy; the single
        #: processor call for npcturn).
        "prompt_content_chars": (
            prompt_metrics["npc_profile_extract"]["scene_facts_chars"]
            + prompt_metrics["npc_profile_update"]["content_chars"]
        ),
        "extract_mentions": [
            str(mention.get("name"))
            for mention in recorded.npc_profile_extract.get("mentions", [])
            if isinstance(mention, dict) and mention.get("name")
        ],
        "exact_mentions": [
            [candidate["npc_id"], surface]
            for candidate in candidates
            if candidate["source"] == "existing"
            and candidate["npc_id"] is not None
            for surface in candidate["mention_names"]
        ],
        "aliases_added": aliases_added,
        "new_profile_names": [profile.name for profile in result.new_profiles],
        "field_deltas": field_deltas,
        "warnings": [
            [
                warning.npc_name,
                warning.field_name,
                warning.code,
                sorted(warning.evidence_ids),
            ]
            for warning in result.warnings
        ],
        "prompt_metrics": prompt_metrics,
    }


def _run_offline_npcturn(
    case: BenchmarkCase,
    recorded: RecordedCase,
) -> dict[str, Any]:
    raise NotImplementedError(
        "the npcturn backend lands with ticket 04 (single NPCTurnProcessor); "
        "until then run with --backend legacy"
    )


_BACKENDS: dict[BACKEND, Any] = {
    "legacy": _run_offline_legacy,
    "npcturn": _run_offline_npcturn,
}


def _current_state(case: BenchmarkCase) -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id=case.scene_id,
            location=case.scene_location,
            participants=[
                ParticipantState(name=name) for name in case.participants
            ],
        ),
        player=PlayerState(name=case.player_name),
    )


def _core_memory(case: BenchmarkCase) -> CoreMemory:
    return CoreMemory(
        items=[
            CoreMemoryItem(**item.model_dump())
            for item in case.core_memory_items
        ]
    )


def _legacy_resolution_signals(
    case: BenchmarkCase,
    recorded: RecordedCase,
    candidates: list[dict],
) -> tuple[dict[str, dict], list[str]]:
    """Build backend-neutral resolution signals from the legacy update batch.

    Returns ``(resolutions, proposal_invariants)`` where ``resolutions`` maps
    candidate name -> ``{"resolution", "target_npc_id"}`` and
    ``proposal_invariants`` carries the legacy recording contract violations
    (non-object results, unknown candidate indices). The scorer consumes only
    these signals, never the raw ``RecordedCase``.
    """
    index_to_name = {candidate["index"]: candidate["name"] for candidate in candidates}
    roster = [profile.npc_id for profile in case.effective_roster()]
    candidate_by_name = {
        candidate["name"]: candidate for candidate in candidates
    }
    resolutions: dict[str, dict] = {}
    proposal_invariants: list[str] = []
    if recorded.npc_profile_update is None:
        return resolutions, proposal_invariants
    for result in recorded.npc_profile_update.get("results", []):
        if not isinstance(result, dict):
            proposal_invariants.append("recorded result is not an object")
            continue
        index = result.get("candidate_index")
        name = index_to_name.get(index)
        if not isinstance(index, int) or name is None:
            proposal_invariants.append(
                f"recorded result references unknown candidate_index {index!r}"
            )
            continue
        resolution = result.get("resolution", "")
        target_npc_id: str | None = None
        if resolution == "existing":
            target_npc_id = candidate_by_name.get(name, {}).get("npc_id")
        elif resolution == "alias_match":
            matched = result.get("matched_npc_index")
            if isinstance(matched, int) and 0 <= matched < len(roster):
                target_npc_id = roster[matched]
        resolutions[name] = {
            "resolution": resolution,
            "target_npc_id": target_npc_id,
        }
    return resolutions, proposal_invariants


def _score_gold(
    case: BenchmarkCase,
    signals: dict,
    call_count_bounds: tuple[int, int],
) -> dict:
    gold = case.gold
    mention_names = set(signals["extract_mentions"])
    resolutions = signals["resolutions"]
    aliases_added = signals["aliases_added"]
    new_names = set(signals["new_profile_names"])
    field_deltas = signals["field_deltas"]
    warnings = signals["warnings"]

    positive: list[tuple[str, bool, bool]] = []
    forbidden_violations: list[str] = []

    for expectation in gold.mention_recall:
        matched = any(
            normalize_key(name) == normalize_key(expectation.name)
            for name in mention_names
        )
        positive.append(("mention_recall", matched, expectation.importance == "must"))
    for name in gold.forbidden_mentions:
        if any(normalize_key(name) == normalize_key(seen) for seen in mention_names):
            forbidden_violations.append(f"forbidden mention extracted: {name}")

    exact_by_npc: dict[str, list[str]] = {}
    for npc_id, surface in signals["exact_mentions"]:
        exact_by_npc.setdefault(npc_id, []).append(surface)
    for expectation in gold.exact_expectations:
        matched = any(
            normalize_key(surface) == normalize_key(expectation.surface)
            for surface in exact_by_npc.get(expectation.npc_id, [])
        )
        positive.append(("exact_existing", matched, expectation.importance == "must"))

    for expectation in gold.resolution_expectations:
        got = resolutions.get(expectation.candidate_name)
        got_resolution = got.get("resolution") if got is not None else None
        matched = got_resolution == expectation.resolution
        if (
            matched
            and expectation.target_npc_id is not None
            and got is not None
            and got.get("target_npc_id") != expectation.target_npc_id
        ):
            matched = False
            forbidden_violations.append(
                f"resolution {expectation.candidate_name!r} alias-matched to "
                f"{got.get('target_npc_id')}, expected "
                f"{expectation.target_npc_id}"
            )
        positive.append(
            (
                "resolution",
                matched,
                expectation.importance == "must",
            )
        )
        if got is None:
            forbidden_violations.append(
                f"resolution missing for candidate {expectation.candidate_name!r}"
            )
    for expectation in gold.forbidden_resolutions:
        got = resolutions.get(expectation.candidate_name)
        if got is not None and got.get("resolution") == expectation.resolution:
            forbidden_violations.append(
                f"forbidden resolution {expectation.candidate_name!r} = "
                f"{expectation.resolution}"
            )

    for expectation in gold.alias_expectations:
        added = aliases_added.get(expectation.npc_id, [])
        matched = any(
            normalize_key(surface) == normalize_key(expectation.surface)
            for surface in added
        )
        positive.append(("alias", matched, expectation.importance == "must"))
    for expectation in gold.forbidden_aliases:
        if any(
            normalize_key(surface) == normalize_key(expectation.surface)
            for surface in aliases_added.get(expectation.npc_id, [])
        ):
            forbidden_violations.append(
                f"forbidden alias added: {expectation.npc_id} + {expectation.surface}"
            )

    for expectation in gold.stable_field_expectations:
        deltas = field_deltas.get(expectation.npc_id, {})
        got = deltas.get(expectation.field)
        matched = got is not None and got == expectation.value
        positive.append(("stable_field", matched, expectation.importance == "must"))
    for expectation in gold.forbidden_field_changes:
        if expectation.field in field_deltas.get(expectation.npc_id, {}):
            forbidden_violations.append(
                f"forbidden field change: {expectation.npc_id}."
                f"{expectation.field}"
            )

    for expectation in gold.new_npc_expectations:
        matched = any(
            normalize_key(name) == normalize_key(expectation.name)
            for name in new_names
        )
        positive.append(("new_npc", matched, expectation.importance == "must"))
    for name in gold.forbidden_new_names:
        if any(normalize_key(name) == normalize_key(created) for created in new_names):
            forbidden_violations.append(f"forbidden new NPC created: {name}")

    warning_keys: set[tuple[str, str, str]] = set()
    for npc_name, field, code, _evidence_ids in warnings:
        warning_keys.add((npc_name, field, code))
    for expectation in gold.warning_expectations:
        profile = next(
            (item for item in case.effective_roster() if item.npc_id == expectation.npc_id),
            None,
        )
        npc_name = profile.name if profile is not None else expectation.npc_id
        matched = (npc_name, expectation.field, expectation.code) in warning_keys
        positive.append(("evidence_warning", matched, expectation.importance == "must"))

    for expectation in gold.no_op_expectations:
        matched = expectation.npc_id not in field_deltas
        positive.append(("no_op", matched, expectation.importance == "must"))

    weighted_total = 0
    weighted_passed = 0
    passed_total: dict[str, int] = {}
    passed_hit: dict[str, int] = {}
    for category, matched, is_must in positive:
        weight = 3 if is_must else 1
        weighted_total += weight
        passed_total[category] = passed_total.get(category, 0) + 1
        if matched:
            weighted_passed += weight
            passed_hit[category] = passed_hit.get(category, 0) + 1

    invariant_violations: list[str] = []
    min_calls, max_calls = call_count_bounds
    if not (min_calls <= signals["call_count"] <= max_calls):
        invariant_violations.append(
            f"backend policy expects {min_calls}..{max_calls} NPC gateway "
            f"calls, got {signals['call_count']}"
        )
    invariant_violations.extend(signals["proposal_invariants"])

    return {
        "weighted_passed": weighted_passed,
        "weighted_total": weighted_total,
        "categories": {
            category: {
                "passed": passed_hit.get(category, 0),
                "total": passed_total.get(category, 0),
            }
            for category in sorted(passed_total)
        },
        "forbidden_violations": sorted(forbidden_violations),
        "invariant_violations": invariant_violations,
    }


def _token_usage(recorded: RecordedCase) -> dict | None:
    if recorded.token_usage is None:
        return None
    return recorded.token_usage


def _token_usage_total(recorded: RecordedCase) -> dict | None:
    """Sum prompt/completion tokens across every task in the recordings entry.

    Task-agnostic: aggregate and scoring never name individual tasks, so the
    single-call NPCTurnProcessor recordings sum identically to the legacy pair.
    """
    if recorded.token_usage is None:
        return None
    totals: dict[str, int] = {}
    for task_usage in recorded.token_usage.values():
        if not isinstance(task_usage, dict):
            continue
        for key in ("prompt_tokens", "completion_tokens"):
            value = task_usage.get(key)
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return dict(sorted(totals.items())) if totals else None


def run_case(
    case: BenchmarkCase,
    recorded: RecordedCase,
    backend: BACKEND,
) -> dict:
    signals = _BACKENDS[backend](case, recorded)
    gold = _score_gold(case, signals, CALL_COUNT_BOUNDS[backend])
    return {
        "case_id": case.case_id,
        "call_count": signals["call_count"],
        "provider_attempts": recorded.provider_attempts(),
        "candidates": signals["candidates"],
        "extract_mentions": signals["extract_mentions"],
        "exact_mentions": signals["exact_mentions"],
        "aliases_added": signals["aliases_added"],
        "new_profile_names": signals["new_profile_names"],
        "field_deltas": signals["field_deltas"],
        "warnings": signals["warnings"],
        "prompt_metrics": signals["prompt_metrics"],
        "prompt_content_chars": signals["prompt_content_chars"],
        "token_usage": _token_usage(recorded),
        "token_usage_total": _token_usage_total(recorded),
        "gold": gold,
    }


def _aggregate(case_reports: list[dict]) -> dict:
    weighted_passed = sum(report["gold"]["weighted_passed"] for report in case_reports)
    weighted_total = sum(report["gold"]["weighted_total"] for report in case_reports)
    forbidden_count = sum(
        len(report["gold"]["forbidden_violations"]) for report in case_reports
    )
    invariant_count = sum(
        len(report["gold"]["invariant_violations"]) for report in case_reports
    )
    categories: dict[str, dict] = {}
    for report in case_reports:
        for category, counts in report["gold"]["categories"].items():
            entry = categories.setdefault(category, {"passed": 0, "total": 0})
            entry["passed"] += counts["passed"]
            entry["total"] += counts["total"]

    token_totals: dict[str, int] = {}
    has_tokens = False
    for report in case_reports:
        usage_total = report.get("token_usage_total") or {}
        for key, value in usage_total.items():
            if isinstance(value, int):
                has_tokens = True
                token_totals[key] = token_totals.get(key, 0) + value

    return {
        "call_count": sum(report["call_count"] for report in case_reports),
        "provider_attempts": sum(
            report["provider_attempts"] for report in case_reports
        ),
        "weighted_passed": weighted_passed,
        "weighted_total": weighted_total,
        "forbidden_violation_count": forbidden_count,
        "invariant_violation_count": invariant_count,
        "categories": categories,
        "prompt_content_chars": sum(
            report["prompt_content_chars"] for report in case_reports
        ),
        "token_usage": dict(sorted(token_totals.items())) if has_tokens else None,
    }


def run_suite(
    fixtures: BenchmarkSuite,
    recordings: RecordingsSuite,
    *,
    backend: BACKEND,
    fixtures_sha256: str,
    recordings_sha256: str,
) -> dict:
    if recordings.backend != backend:
        raise ValueError(
            f"recordings backend {recordings.backend!r} does not match "
            f"execution backend {backend!r}"
        )
    recorded_by_case = recordings.cases
    case_reports: list[dict] = []
    for case in fixtures.cases:
        recorded = recorded_by_case.get(case.case_id)
        if recorded is None:
            raise ValueError(
                f"recordings for backend {backend!r} are missing case "
                f"{case.case_id!r}"
            )
        case_reports.append(run_case(case, recorded, backend))
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "backend": backend,
        "fixtures_sha256": fixtures_sha256,
        "recordings_sha256": recordings_sha256,
        "aggregate": _aggregate(case_reports),
        "cases": case_reports,
    }


def _case_map(report: dict) -> dict[str, dict]:
    return {case["case_id"]: case for case in report["cases"]}


def compare_reports(current: dict, baseline: dict) -> tuple[list[str], bool]:
    if baseline.get("benchmark_version") != BENCHMARK_VERSION:
        raise ValueError("baseline benchmark version does not match runner")
    if baseline.get("fixtures_sha256") != current["fixtures_sha256"]:
        raise ValueError("fixtures.json changed; review cases before updating baseline")

    snapshot_changed = (
        baseline.get("backend") != current["backend"]
        or baseline.get("recordings_sha256") != current["recordings_sha256"]
    )

    lines: list[str] = []
    regression = False
    baseline_cases = _case_map(baseline)
    for case in current["cases"]:
        before = baseline_cases.get(case["case_id"])
        if before is None:
            raise ValueError(f"baseline is missing case {case['case_id']}")
        changed = case != before
        gold_before = before["gold"]
        gold_current = case["gold"]
        if (
            gold_current["weighted_passed"] < gold_before["weighted_passed"]
            or len(gold_current["forbidden_violations"])
            > len(gold_before["forbidden_violations"])
            or len(gold_current["invariant_violations"])
            > len(gold_before["invariant_violations"])
        ):
            regression = True
        lines.append(
            f"{case['case_id']:<36} "
            f"weighted {gold_before['weighted_passed']}/{gold_before['weighted_total']}"
            f"->{gold_current['weighted_passed']}/{gold_current['weighted_total']}  "
            f"forbidden {len(gold_before['forbidden_violations'])}"
            f"->{len(gold_current['forbidden_violations'])}  "
            f"calls {before['call_count']}->{case['call_count']}"
            f"{'  CHANGED' if changed else ''}"
        )
    if snapshot_changed:
        lines.append(
            "note: backend/recordings snapshot differs from baseline "
            f"(baseline backend={baseline.get('backend')!r}, "
            f"current backend={current['backend']!r}); run --update-baseline "
            "after reviewing the intentional change"
        )
    return lines, regression


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _record_case(case: BenchmarkCase, trace_id: str, trace_path: Path) -> RecordedCase:
    """Run the real two-task pipeline and return the recorded outputs."""
    gateway = _gateway_for_case(trace_path)
    evidence_documents = {
        document.document_id: document
        for document in build_turn_documents(
            scene_id=case.scene_id,
            turn_index=case.turn_index,
            user_input=case.user_input,
            scene_text=case.scene_text,
        )
    }
    with NPCProfileStore(":memory:") as store:
        for profile in case.effective_roster():
            store.upsert(profile.to_profile())
        update_npc_profiles_workflow(
            scene_facts=case.scene_facts,
            current_state=_current_state(case),
            core_memory=_core_memory(case),
            gateway=gateway,
            npc_store=store,
            evidence_documents=evidence_documents,
            turn_index=case.turn_index,
            recent_summaries=[],
            trace_id=trace_id,
        )
    records = _trace_records_for(case.case_id, trace_id, gateway)
    extract = records.get("npc_profile_extract")
    update = records.get("npc_profile_update")
    if extract is None:
        raise RuntimeError(
            f"case {case.case_id!r}: missing recorded LLM output for "
            "['npc_profile_extract']"
        )
    if update is None and extract["parsed_output"].get("mentions"):
        raise RuntimeError(
            f"case {case.case_id!r}: missing recorded LLM output for "
            "['npc_profile_update'] despite non-empty extraction"
        )
    token_usage = {
        task: (
            records[task].get("token_usage")
            if task in records
            else None
        )
        for task in ("npc_profile_extract", "npc_profile_update")
    }
    attempts = {
        task: records[task]["attempts"]
        for task in ("npc_profile_extract", "npc_profile_update")
        if task in records
    }
    return RecordedCase(
        npc_profile_extract=extract["parsed_output"],
        npc_profile_update=(
            update["parsed_output"] if update is not None else None
        ),
        token_usage=token_usage,
        attempts=attempts or None,
    )


def _selected_cases(
    fixtures: BenchmarkSuite,
    only: set[str],
) -> list[BenchmarkCase]:
    if not only:
        return list(fixtures.cases)
    selected = [case for case in fixtures.cases if case.case_id in only]
    missing = only - {case.case_id for case in fixtures.cases}
    if missing:
        raise ValueError(f"unknown case ids: {sorted(missing)}")
    return selected


def _expected_update_input(case: BenchmarkCase) -> dict[str, str]:
    """Recompute the deterministic update-prompt inputs from the fixture.

    Mirrors ``NPCProfileUpdater.update()`` so a replay can verify that a trace
    was produced from the CURRENT fixture and not a stale one.
    """
    evidence_documents = {
        document.document_id: document
        for document in build_turn_documents(
            scene_id=case.scene_id,
            turn_index=case.turn_index,
            user_input=case.user_input,
            scene_text=case.scene_text,
        )
    }
    scene_paragraph_evidence = [
        document
        for document in evidence_documents.values()
        if document.source_type == "scene_paragraph"
    ]
    with NPCProfileStore(":memory:") as store:
        for profile in case.effective_roster():
            store.upsert(profile.to_profile())
        existing_profiles = store.list_all()
    current_state = _current_state(case)
    return {
        "core_memory_json": _core_memory(case).model_dump_json(indent=2),
        "scene_facts_json": case.scene_facts,
        "current_state_summary_json": project_current_state(current_state),
        "recent_summaries_json": project_scene_summaries([]),
        "scene_paragraph_evidence_json": json.dumps(
            [
                {"document_id": document.document_id, "text": document.text}
                for document in scene_paragraph_evidence
            ],
            ensure_ascii=False,
            indent=2,
        ),
        "existing_npc_options_json": json.dumps(
            [
                *(
                    _existing_option_for_prompt(index, profile)
                    for index, profile in enumerate(existing_profiles)
                ),
                {
                    "npc_index": len(existing_profiles),
                    "name": current_state.player.name,
                    "aliases": [],
                    "role": None,
                    "public_summary": None,
                    "appearance": None,
                    "faction": None,
                    "last_known_location": None,
                },
            ],
            ensure_ascii=False,
            indent=2,
        ),
    }


def _replay_trace(
    trace_path: Path,
    fixtures: BenchmarkSuite,
    *,
    only: set[str],
) -> RecordingsSuite:
    """Rebuild recordings from an existing trace file, retries included.

    Each trace entry is grouped by ``trace_id``; a case id is recovered from
    the ``bench_<run_token>_<case_id>`` prefix, so this works on any run's
    trace. Only cases present in the fixtures (and ``only`` when set) are kept.

    Guards against stale or ambiguous traces: the file must contain a single
    run, every fixture case must be present, and each case's recorded inputs
    must match what the CURRENT fixture would produce. A mismatch raises with
    the offending case id so it can be re-recorded with ``--record --only``.
    """
    entries = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_case: dict[str, list[dict]] = {}
    run_tokens: set[str] = set()
    for entry in entries:
        trace_id = entry.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id.startswith("bench_"):
            continue
        parts = trace_id.split("_", 2)
        if len(parts) < 3:
            continue
        _, run_token, case_id = parts
        run_tokens.add(run_token)
        by_case.setdefault(case_id, []).append(entry)

    if len(run_tokens) > 1:
        raise ValueError(
            f"trace contains {len(run_tokens)} runs "
            f"({sorted(run_tokens)}); pass a single run's trace file"
        )

    selected = _selected_cases(fixtures, only)
    allowed = {case.case_id for case in selected}
    missing = allowed - set(by_case)
    if missing:
        raise ValueError(
            f"trace is missing cases: {sorted(missing)}; "
            "re-record them with --record --only"
        )

    recorded_cases: dict[str, RecordedCase] = {}
    for case in selected:
        case_entries = by_case[case.case_id]
        records = _trace_task_records(case_entries)
        extract = records.get("npc_profile_extract")
        if extract is None:
            raise ValueError(
                f"trace has no valid extract output for case {case.case_id!r}"
            )
        _verify_trace_input(case, case_entries)
        update = records.get("npc_profile_update")
        token_usage = {
            task: (
                records[task].get("token_usage")
                if task in records
                else None
            )
            for task in ("npc_profile_extract", "npc_profile_update")
        }
        attempts = {
            task: records[task]["attempts"]
            for task in ("npc_profile_extract", "npc_profile_update")
            if task in records
        }
        recorded_cases[case.case_id] = RecordedCase(
            npc_profile_extract=extract["parsed_output"],
            npc_profile_update=(
                update["parsed_output"] if update is not None else None
            ),
            token_usage=token_usage,
            attempts=attempts or None,
        )
    return RecordingsSuite(
        version=1,
        backend="legacy",
        cases=recorded_cases,
    )


def _verify_trace_input(case: BenchmarkCase, case_entries: list[dict]) -> None:
    """Fail when the trace's recorded inputs do not match the current fixture."""
    extract_input: dict | None = None
    update_input: dict | None = None
    for entry in case_entries:
        task = entry.get("task")
        input_data = entry.get("input_data")
        if not isinstance(input_data, dict):
            continue
        if task == "npc_profile_extract" and extract_input is None:
            extract_input = input_data
        elif task == "npc_profile_update" and update_input is None:
            update_input = input_data

    stale: list[str] = []
    if extract_input is None or extract_input.get("scene_facts_json") != case.scene_facts:
        stale.append("npc_profile_extract scene_facts")
    if update_input is not None:
        expected = _expected_update_input(case)
        for key, value in expected.items():
            if update_input.get(key) != value:
                stale.append(f"npc_profile_update {key}")
    if stale:
        raise ValueError(
            f"trace inputs for case {case.case_id!r} are stale "
            f"(current fixture differs: {', '.join(stale)}); "
            "re-record with --record --only <case_id>"
        )


def _record_all(
    fixtures: BenchmarkSuite,
    recordings_path: Path,
    run_token: str,
    *,
    only: set[str],
) -> RecordingsSuite:
    trace_dir = ROOT / ".bench" / "npc_baseline" / f"run-{run_token}"
    trace_path = trace_dir / "traces.jsonl"
    recorded_cases: dict[str, RecordedCase] = {}
    for case in _selected_cases(fixtures, only):
        trace_id = f"bench_{run_token}_{case.case_id}"
        recorded_cases[case.case_id] = _record_case(case, trace_id, trace_path)
    return RecordingsSuite(
        version=1,
        backend="legacy",
        cases=recorded_cases,
    )


def _write_recordings(
    path: Path,
    suite: RecordingsSuite,
    *,
    merge_existing: bool,
) -> None:
    if merge_existing and path.exists():
        existing = load_recordings(path)
        existing.cases.update(suite.cases)
        _write_json(path, existing.model_dump())
        return
    _write_json(path, suite.model_dump())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replayable NPC pipeline benchmark. Offline by default; --record "
            "refreshes recorded LLM outputs and tokens via a real gateway."
        )
    )
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--backend", choices=("legacy", "npcturn"), default="legacy")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--replay-trace", type=Path)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    fixtures = load_fixtures(args.fixtures)

    if args.record and args.replay_trace:
        raise SystemExit("--record and --replay-trace are mutually exclusive")
    if args.only and not (args.record or args.replay_trace):
        raise SystemExit("--only requires --record or --replay-trace")
    if args.recordings.exists() and not args.overwrite and (args.record or args.replay_trace):
        raise SystemExit(
            f"recordings file already exists: {args.recordings}\n"
            "pass --overwrite to replace it"
        )

    if args.replay_trace:
        if args.backend != "legacy":
            raise RuntimeError("--replay-trace supports only the legacy backend")
        recordings_suite = _replay_trace(
            args.replay_trace,
            fixtures,
            only=set(args.only),
        )
        _write_recordings(
            args.recordings,
            recordings_suite,
            merge_existing=bool(args.only),
        )
        print(
            f"replayed {len(recordings_suite.cases)} cases from "
            f"{args.replay_trace} -> {args.recordings}"
        )
        print("review the diff, then run --update-baseline")
    elif args.record:
        if args.backend != "legacy":
            raise RuntimeError("--record currently supports only the legacy backend")
        run_token = uuid.uuid4().hex[:12]
        recordings_suite = _record_all(
            fixtures,
            args.recordings,
            run_token,
            only=set(args.only),
        )
        _write_recordings(
            args.recordings,
            recordings_suite,
            merge_existing=bool(args.only),
        )
        print(
            f"recorded {len(recordings_suite.cases)} cases (run {run_token}) "
            f"-> {args.recordings}"
        )
        print("review the diff, then run --update-baseline")

    recordings = load_recordings(args.recordings)
    report = run_suite(
        fixtures,
        recordings,
        backend=args.backend,
        fixtures_sha256=_fixtures_fingerprint(args.fixtures),
        recordings_sha256=_recordings_fingerprint(args.recordings),
    )
    if args.json_out:
        _write_json(args.json_out, report)

    if args.update_baseline:
        _write_json(args.baseline, report)
        print(f"updated baseline: {args.baseline}")
        return 0

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    lines, regression = compare_reports(report, baseline)
    print("\n".join(lines))
    aggregate = report["aggregate"]
    token = aggregate["token_usage"]
    token_text = (
        " ".join(f"{key}={value}" for key, value in sorted(token.items()))
        if token
        else "token usage not recorded"
    )
    print(
        "aggregate: "
        f"weighted={aggregate['weighted_passed']}/{aggregate['weighted_total']} "
        f"forbidden={aggregate['forbidden_violation_count']} "
        f"invariant={aggregate['invariant_violation_count']} "
        f"calls={aggregate['call_count']} "
        f"attempts={aggregate['provider_attempts']} "
        f"prompt_content_chars={aggregate['prompt_content_chars']} "
        f"({token_text})"
    )
    if args.check and report != baseline:
        print("benchmark output differs from the accepted baseline")
        return 1
    if regression:
        print("quality regression detected")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
