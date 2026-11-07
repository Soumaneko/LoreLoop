"""Associate Composer's archive names with profiles and Scene Facts evidence."""
from __future__ import annotations

from dataclasses import dataclass
from app.core.text import normalize_compact_key
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_facts.schemas import NpcPresence, SceneNpcMention
from app.domain.scene_writer.schemas import NPCUsage


@dataclass(frozen=True)
class ResolvedNpc:
    npc_id: str
    canonical_name: str
    presence: NpcPresence
    primary_surfaces: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()
    evidence_paragraph_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityResolutionDiagnostics:
    groups: int
    resolved_groups: int
    unmatched_groups: int
    appeared_groups: int
    mentioned_only_groups: int


@dataclass(frozen=True)
class NpcIdentityResolution:
    resolved: tuple[ResolvedNpc, ...] = ()
    diagnostics: IdentityResolutionDiagnostics | None = None


def resolve_npc_identities(
    *, profiles: list[NPCProfile], npc_usages: list[NPCUsage],
    npc_mentions: list[SceneNpcMention], player_name: str,
) -> NpcIdentityResolution:
    player = normalize_compact_key(player_name)
    by_name = {normalize_compact_key(profile.name): profile for profile in profiles}
    evidence = {normalize_compact_key(item.primary_surface): item for item in npc_mentions}
    resolved = {}
    unmatched = 0
    for usage in npc_usages:
        key = normalize_compact_key(usage.name)
        if key == player:
            continue
        profile = by_name.get(key)
        if profile is None:
            unmatched += 1
            continue
        old = resolved.get(profile.npc_id)
        mention = evidence.get(key)
        surfaces = list(dict.fromkeys([
            *(old.primary_surfaces if old else ()), *usage.surfaces,
        ]))
        resolved[profile.npc_id] = ResolvedNpc(
            npc_id=profile.npc_id, canonical_name=profile.name,
            presence="appeared" if usage.presence == "appeared" or (old and old.presence == "appeared") else "mentioned_only",
            primary_surfaces=tuple(surfaces),
            evidence_fact_ids=tuple(mention.evidence_fact_ids) if mention else (),
            evidence_paragraph_ids=tuple(mention.evidence_paragraph_ids) if mention else (),
        )
    values = tuple(sorted(resolved.values(), key=lambda item: (item.canonical_name, item.npc_id)))
    appeared = sum(item.presence == "appeared" for item in values)
    return NpcIdentityResolution(resolved=values, diagnostics=IdentityResolutionDiagnostics(
        groups=len(npc_usages), resolved_groups=len(values), unmatched_groups=unmatched,
        appeared_groups=appeared, mentioned_only_groups=len(values)-appeared,
    ))


def project_used_npcs(npc_usages, profiles, *, context_profiles=(), materials=()) -> list[dict]:
    """Preserve the existing basic profile view for the complete used roster."""
    by_name = {}
    for profile in [*profiles, *context_profiles]:
        by_name.setdefault(normalize_compact_key(profile.name), {
            "name": profile.name, "role": profile.role or "",
            "appearance": profile.appearance or "",
            "personality_trait": profile.personality_trait or "",
        })
    for material in materials:
        if material.kind == "npc" and material.payload.get("source") in {"director_npc", "spontaneous"}:
            data = material.payload
            by_name.setdefault(normalize_compact_key(data.get("name")), {
                key: data.get(key) or "" for key in ("name", "role", "appearance", "personality_trait")
            })
    entries = {}
    for usage in npc_usages:
        key = normalize_compact_key(usage.name)
        profile = by_name.get(key)
        if profile is None:
            continue
        old = entries.get(key)
        entries[key] = {
            **profile,
            "surfaces": list(dict.fromkeys([*(old["surfaces"] if old else []), *usage.surfaces])),
            "presence": "appeared" if usage.presence == "appeared" or (old and old["presence"] == "appeared") else "mentioned_only",
        }
    return list(entries.values())


def archive_alias_surfaces(surfaces) -> list[str]:
    """Scene pronouns stay in the receipt, not in the archive name index."""
    pronouns = {"他", "她", "它", "他们", "她们", "它们", "对方", "那人", "此人"}
    return list(dict.fromkeys(
        surface.strip() for surface in surfaces
        if normalize_compact_key(surface) and normalize_compact_key(surface) not in pronouns
    ))


def surface_is_known(surface: str, profile: NPCProfile) -> bool:
    key = normalize_compact_key(surface)
    return key == normalize_compact_key(profile.name) or any(
        normalize_compact_key(alias) == key for alias in profile.aliases
    )
