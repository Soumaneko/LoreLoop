"""Trusted field-log evidence pointers for Context Facts retrieval.

This module owns the retrieval-side contract for evidence-backed NPC field
changes: a read-only ``NPCFieldHistoryLink`` and the ``NPCProfileHistoryBridge``
that resolves a search plan into those links.  It deliberately does not import
anything from ``context_facts``: the search plan is consumed duck-typed through
a minimal protocol, and normalization into the internal ``RelationEvidenceLink``
happens in the adapter layer, so the domain store schema stays free of reverse
dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.core.text import normalize_key
from app.domain.confirmed_history.text import support_score
from app.domain.npc_profile.field_history import (
    NPCFieldChangeLog,
    NPCFieldHistoryName,
)
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore

#: Minimum lexical support for an explicit-input topic to count as a
#: deterministic match against a node's summary delta / field value.  Kept
#: independent of the evidence-support ``WEAK_SUPPORT`` anchor so the two
#: domains can tune separately.
NPC_TOPIC_MATCH_THRESHOLD = 0.30

#: Minimum normalized topic length for topic-triggered eligibility.
NPC_TOPIC_MIN_CHARS = 2

#: Deterministic field ordering for bridge emission.  ``public_summary`` 是
#: v7 遗留字段，其日志只读保留为 audit row，不再桥接发射。
_FIELD_ORDER: tuple[NPCFieldHistoryName, ...] = (
    "role",
    "appearance",
    "faction",
    "last_scene_summary",
)


NPCFieldRelationReason = Literal[
    "npc_name_match",
    "npc_alias_match",
    "npc_delta_topic_match",
    "npc_value_topic_match",
]


def _node_change_basis(
    log: NPCFieldChangeLog,
) -> tuple[str | None, str | None, NPCFieldRelationReason, bool]:
    """Node-specific this-change basis for one field log.

    ``last_scene_summary`` is a per-scene whole replacement: the full new value
    is the node semantics (and its own delta), always complete.  Accumulative
    stable fields use the persisted ``change_basis`` written by the batch
    updater at accepted-change time -- the trusted statement of what this change
    added/removed/rewrote -- with NO full-value context, so retained old facts
    can neither trigger nor support a later node.  Legacy logs without a
    persisted basis are marked ``basis_complete=False`` (incomplete, degraded
    rescue only) instead of pretending a complete delta.
    """
    if log.field_name == "last_scene_summary":
        return log.new_value, None, "npc_delta_topic_match", True
    if log.change_basis and log.change_basis.strip():
        return log.change_basis, None, "npc_delta_topic_match", True
    return log.new_value, None, "npc_value_topic_match", False


class NPCFieldHistoryLink(BaseModel):
    """A structural pointer from one accepted field log into Confirmed History.

    ``evidence_basis_primary`` is the node-specific semantic basis (the
    ``new_value`` of the field change); ``old_value`` never enters the basis so a
    later aggregate cannot broadcast onto an older node.  The basis is unbounded
    because it only feeds lexical matching and never enters the LLM payload.
    ``intent_match_reasons`` keeps the per-intent reason set so the shared
    admission pass never broadcasts one intent's reason onto another.
    """

    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    field_name: NPCFieldHistoryName
    log_id: int = Field(ge=1)
    turn_index: int = Field(ge=0)
    evidence_id: str = Field(min_length=1)
    evidence_hash: str | None = Field(default=None, min_length=64, max_length=64)
    evidence_position: int = Field(ge=0)
    triggering_intent_ids: list[str] = Field(min_length=1)
    match_reasons: list[NPCFieldRelationReason] = Field(min_length=1)
    intent_match_reasons: dict[str, list[NPCFieldRelationReason]] = Field(
        default_factory=dict
    )
    evidence_basis_primary: str | None = None
    evidence_basis_context: str | None = None
    basis_complete: bool = True
    source: Literal["npc_profile"] = "npc_profile"


class NPCFieldHistoryBridgeResult(BaseModel):
    """Read-only bridge output: normalized links plus matched profile ids."""

    model_config = ConfigDict(extra="forbid")

    links: list[NPCFieldHistoryLink] = Field(default_factory=list)
    matched_profile_ids: list[str] = Field(default_factory=list)


class _Emission:
    """One merged link entry shared across the intents that triggered it."""

    key: tuple[str, NPCFieldHistoryName, int, str, int]
    npc_id: str
    field_name: NPCFieldHistoryName
    log: NPCFieldChangeLog
    evidence_hash: str | None
    intent_reasons: dict[str, set[NPCFieldRelationReason]]
    is_identity: bool
    min_intent_order: int

    __slots__ = (
        "key",
        "npc_id",
        "field_name",
        "log",
        "evidence_hash",
        "intent_reasons",
        "is_identity",
        "min_intent_order",
    )

    def __init__(
        self,
        key: tuple[str, NPCFieldHistoryName, int, str, int],
        npc_id: str,
        field_name: NPCFieldHistoryName,
        log: NPCFieldChangeLog,
        evidence_hash: str | None,
    ) -> None:
        self.key = key
        self.npc_id = npc_id
        self.field_name = field_name
        self.log = log
        self.evidence_hash = evidence_hash
        self.intent_reasons: dict[str, set[NPCFieldRelationReason]] = {}
        self.is_identity = False
        self.min_intent_order = 1_000_000_000


class _IntentSurface(Protocol):
    name: str
    aliases: Sequence[str]


class _BridgeIntent(Protocol):
    intent_id: str
    origin: str
    entities: Sequence[_IntentSurface]
    topics: Sequence[str]


def _logs_by_field(
    logs: list[NPCFieldChangeLog],
) -> dict[NPCFieldHistoryName, list[NPCFieldChangeLog]]:
    grouped: dict[NPCFieldHistoryName, list[NPCFieldChangeLog]] = defaultdict(list)
    for log in logs:
        grouped[log.field_name].append(log)
    return dict(grouped)


class NPCProfileHistoryBridge:
    """Resolve a search plan into trusted NPC field-log evidence pointers.

    Identity matches use name / permanent alias exact keys (transient aliases
    are not a stable history index); a surface mapping to several profiles
    emits all of them deterministically.  An exact identity skeleton takes the
    latest node per open field (the current-state default); every deterministically
    topic-matched node -- not a fixed number -- also enters emission, and the
    downstream node-evidence support layer plus selector budgets decide what
    survives.  Topics are scoped with AND semantics: an intent that carries
    entities only matches topics inside its own identity matches, while a
    pure-topic intent scans every active profile.  No full current profile is
    attached as a contextual basis, so a later aggregate never broadcasts onto
    an older node.
    """

    def __init__(
        self,
        npc_store: NPCProfileStore,
    ):
        self.npc_store = npc_store

    def retrieve(
        self,
        search_plan: Any,
    ) -> NPCFieldHistoryBridgeResult:
        # ``search_plan`` is duck-typed: only ``intents`` is read, and each
        # intent only through ``intent_id`` / ``origin`` / ``entities``
        # (name, aliases) / ``topics``. This keeps the bridge free of a
        # reverse dependency on the context_facts schema.
        profiles = self.npc_store.list_all(active_only=False)
        profiles_by_id: dict[str, NPCProfile] = {
            profile.npc_id: profile for profile in profiles
        }

        identity_index: dict[str, set[str]] = defaultdict(set)
        for profile in profiles:
            for surface in [profile.name, *profile.aliases]:
                key = normalize_key(surface)
                if key:
                    identity_index[key].add(profile.npc_id)

        logs_by_npc: dict[str, list[NPCFieldChangeLog]] = defaultdict(list)
        for log in self.npc_store.list_field_change_logs():
            logs_by_npc[log.npc_id].append(log)

        intent_order = {
            intent.intent_id: index
            for index, intent in enumerate(search_plan.intents)
        }

        emissions: dict[tuple[str, NPCFieldHistoryName, int, str, int], _Emission] = {}

        for intent in search_plan.intents:
            identity_ids = self._identity_matches(
                intent,
                identity_index,
                profiles_by_id,
            )
            # AND semantics: an entity-carrying intent only matches topics
            # inside its own identity hits; a pure-topic intent scans globally.
            scope = identity_ids if intent.entities else None
            topic_ids = self._topic_matched_profile_ids(
                intent,
                logs_by_npc,
                profiles_by_id,
                scope,
            )
            for npc_id in sorted(identity_ids | topic_ids):
                profile = profiles_by_id[npc_id]
                field_logs = _logs_by_field(logs_by_npc.get(npc_id, []))
                if not field_logs:
                    continue
                identity_reasons = (
                    self._identity_reasons(intent, profile)
                    if npc_id in identity_ids
                    else set()
                )
                topic_reasons_by_log = (
                    self._topic_matches(intent, field_logs)
                    if profile.active
                    else {}
                )
                self._collect_emissions(
                    emissions=emissions,
                    intent_id=intent.intent_id,
                    intent_order=intent_order[intent.intent_id],
                    npc_id=npc_id,
                    field_logs=field_logs,
                    identity_reasons=identity_reasons,
                    topic_reasons_by_log=topic_reasons_by_log,
                )

        field_index = {
            field_name: index for index, field_name in enumerate(_FIELD_ORDER)
        }
        ordered = sorted(
            emissions.values(),
            key=lambda emission: (
                not emission.is_identity,
                emission.min_intent_order,
                emission.npc_id,
                -emission.log.turn_index,
                -emission.log.log_id,
                field_index[emission.field_name],
                emission.key[4],
            ),
        )

        links: list[NPCFieldHistoryLink] = []
        for emission in ordered:
            basis_primary, basis_context, _, basis_complete = (
                _node_change_basis(emission.log)
            )
            union_reasons = set().union(*(
                reasons for reasons in emission.intent_reasons.values()
            ))
            links.append(NPCFieldHistoryLink(
                npc_id=emission.npc_id,
                field_name=emission.field_name,
                log_id=emission.log.log_id,
                turn_index=emission.log.turn_index,
                evidence_id=emission.key[3],
                evidence_hash=emission.evidence_hash,
                evidence_position=emission.key[4],
                triggering_intent_ids=sorted(
                    emission.intent_reasons,
                    key=lambda intent_id: (
                        intent_order[intent_id],
                        intent_id,
                    ),
                ),
                match_reasons=sorted(union_reasons),
                intent_match_reasons={
                    intent_id: sorted(reasons)
                    for intent_id, reasons in sorted(
                        emission.intent_reasons.items(),
                        key=lambda item: (
                            intent_order[item[0]],
                            item[0],
                        ),
                    )
                },
                evidence_basis_primary=basis_primary,
                evidence_basis_context=basis_context,
                basis_complete=basis_complete,
            ))

        return NPCFieldHistoryBridgeResult(
            links=links,
            matched_profile_ids=sorted({link.npc_id for link in links}),
        )

    def _identity_matches(
        self,
        intent: _BridgeIntent,
        identity_index: dict[str, set[str]],
        profiles_by_id: dict[str, NPCProfile],
    ) -> set[str]:
        matches: set[str] = set()
        for entity in intent.entities:
            for surface in [entity.name, *entity.aliases]:
                key = normalize_key(surface)
                if not key:
                    continue
                for npc_id in identity_index.get(key, ()):
                    profile = profiles_by_id[npc_id]
                    if not profile.active and intent.origin != "explicit_input":
                        continue
                    matches.add(npc_id)
        return matches

    def _identity_reasons(
        self,
        intent: _BridgeIntent,
        profile: NPCProfile,
    ) -> set[NPCFieldRelationReason]:
        reasons: set[NPCFieldRelationReason] = set()
        name_key = normalize_key(profile.name)
        alias_keys = {normalize_key(alias) for alias in profile.aliases}
        for entity in intent.entities:
            for surface in [entity.name, *entity.aliases]:
                key = normalize_key(surface)
                if not key:
                    continue
                if key == name_key:
                    reasons.add("npc_name_match")
                elif key in alias_keys:
                    reasons.add("npc_alias_match")
        return reasons

    def _topic_matched_profile_ids(
        self,
        intent: _BridgeIntent,
        logs_by_npc: dict[str, list[NPCFieldChangeLog]],
        profiles_by_id: dict[str, NPCProfile],
        scope: set[str] | None,
    ) -> set[str]:
        matched: set[str] = set()
        if intent.origin != "explicit_input" or not getattr(intent, "topics", None):
            return matched
        candidate_ids = scope if scope is not None else set(profiles_by_id)
        for npc_id in candidate_ids:
            profile = profiles_by_id[npc_id]
            if not profile.active:
                continue
            field_logs = _logs_by_field(logs_by_npc.get(npc_id, []))
            if field_logs and self._topic_matches(intent, field_logs):
                matched.add(npc_id)
        return matched

    def _topic_matches(
        self,
        intent: _BridgeIntent,
        field_logs: dict[NPCFieldHistoryName, list[NPCFieldChangeLog]],
    ) -> dict[int, set[NPCFieldRelationReason]]:
        reasons_by_log: dict[int, set[NPCFieldRelationReason]] = defaultdict(set)
        if intent.origin != "explicit_input" or not getattr(intent, "topics", None):
            return dict(reasons_by_log)
        for topic in intent.topics:
            normalized = str(topic).strip()
            if len(normalized) < NPC_TOPIC_MIN_CHARS:
                continue
            for field_name, logs in field_logs.items():
                # v7 legacy ``public_summary`` 日志只读保留、不再参与主题匹配，
                # 既不能单独授予档案主题命中，也不进入发射。
                if field_name not in _FIELD_ORDER:
                    continue
                for log in logs:
                    basis, _, reason, _ = _node_change_basis(log)
                    if basis and support_score(
                        normalized,
                        basis,
                    ) >= NPC_TOPIC_MATCH_THRESHOLD:
                        reasons_by_log[log.log_id].add(reason)
        return dict(reasons_by_log)

    def _collect_emissions(
        self,
        *,
        emissions: dict[tuple[str, NPCFieldHistoryName, int, str, int], _Emission],
        intent_id: str,
        intent_order: int,
        npc_id: str,
        field_logs: dict[NPCFieldHistoryName, list[NPCFieldChangeLog]],
        identity_reasons: set[NPCFieldRelationReason],
        topic_reasons_by_log: dict[int, set[NPCFieldRelationReason]],
    ) -> None:
        selected_nodes: list[tuple[NPCFieldHistoryName, NPCFieldChangeLog]] = []
        identity_log_ids: set[int] = set()
        for field_name in _FIELD_ORDER:
            logs = field_logs.get(field_name)
            if not logs:
                continue
            ordered = sorted(
                logs,
                key=lambda log: (-log.turn_index, -log.log_id),
            )
            if identity_reasons:
                identity_log_ids.add(ordered[0].log_id)
            for node in self._select_field_nodes(
                logs,
                identity_triggered=bool(identity_reasons),
                topic_reasons_by_log=topic_reasons_by_log,
            ):
                selected_nodes.append((field_name, node))
        for field_name, node in selected_nodes:
            node_reasons = set(topic_reasons_by_log.get(node.log_id, set()))
            if node.log_id in identity_log_ids:
                node_reasons.update(identity_reasons)
            if not node_reasons:
                continue
            for evidence in sorted(node.evidence, key=lambda item: item.position):
                key = (
                    npc_id,
                    field_name,
                    node.log_id,
                    evidence.evidence_id,
                    evidence.position,
                )
                emission = emissions.get(key)
                if emission is None:
                    emission = _Emission(
                        key=key,
                        npc_id=npc_id,
                        field_name=field_name,
                        log=node,
                        evidence_hash=evidence.evidence_hash,
                    )
                    emissions[key] = emission
                emission.intent_reasons.setdefault(intent_id, set()).update(
                    node_reasons
                )
                if node.log_id in identity_log_ids:
                    emission.is_identity = True
                if intent_order < emission.min_intent_order:
                    emission.min_intent_order = intent_order

    def _select_field_nodes(
        self,
        logs: list[NPCFieldChangeLog],
        *,
        identity_triggered: bool,
        topic_reasons_by_log: dict[int, set[NPCFieldRelationReason]],
    ) -> list[NPCFieldChangeLog]:
        """Select the nodes this intent may emit for one field.

        Identity-triggered intents always emit the latest node (the
        current-state default); every deterministically topic-matched node is
        also emitted -- no fixed per-field cap -- because each triggered node
        is a distinct appearance -> evidence mapping that must reach the
        node-evidence support layer before any budget competition.
        """
        ordered = sorted(
            logs,
            key=lambda log: (-log.turn_index, -log.log_id),
        )
        selected: list[NPCFieldChangeLog] = []
        selected_ids: set[int] = set()
        if identity_triggered:
            selected.append(ordered[0])
            selected_ids.add(ordered[0].log_id)
        selected.extend(
            log
            for log in ordered
            if log.log_id in topic_reasons_by_log
            and log.log_id not in selected_ids
        )
        return selected
