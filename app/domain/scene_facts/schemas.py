from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.text import normalize_compact_key
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState


NpcMentionBasis = Literal[
    "explicit_fixed_alias",
    "temporary_address",
    "same_scene_coreference",
]


class AliasRelation(BaseModel):
    """正文表达的 alias 关系：只陈述「同一人」，不判定档案/持久化。

    ``explicit_fixed_alias`` 会被确定性持久化，因此必须携带至少一条
    ``evidence_fact_id``（provenance 比普通提示字段更严格）。
    """

    model_config = ConfigDict(extra="forbid")

    surface: str = Field(min_length=1)
    basis: NpcMentionBasis
    evidence_fact_ids: list[str] = Field(default_factory=list)

    @field_validator("surface")
    @classmethod
    def _strip_surface(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("surface must not be blank")
        return clean

    @model_validator(mode="after")
    def _fixed_alias_requires_evidence(self):
        if self.basis == "explicit_fixed_alias" and not self.evidence_fact_ids:
            raise ValueError(
                "explicit_fixed_alias requires at least one evidence_fact_id"
            )
        return self


class SceneNpcMention(BaseModel):
    """本章出现的一个 NPC 人物簇：表面称呼 + 场景内 alias 关系。

    ``observed_surfaces`` 必须包含 ``primary_surface``。``evidence_fact_ids`` /
    ``evidence_paragraph_ids`` 为人物出现的证据（mention-level，fact 引用与正文
    段落 document 引用，与 alias relation 的证据相互独立）。Scene Facts 只陈述
    正文表达了「这些称呼是同一人」，不判定 npc_id / existing / new / 是否建档 /
    是否持久化。
    """

    model_config = ConfigDict(extra="forbid")

    primary_surface: str = Field(min_length=1)
    observed_surfaces: list[str] = Field(default_factory=list)
    alias_relations: list[AliasRelation] = Field(default_factory=list)
    evidence_fact_ids: list[str] = Field(default_factory=list)
    evidence_paragraph_ids: list[str] = Field(default_factory=list)

    @field_validator("primary_surface")
    @classmethod
    def _strip_primary(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("primary_surface must not be blank")
        return clean

    @field_validator("observed_surfaces")
    @classmethod
    def _strip_observed(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("observed_surfaces must be unique")
        return cleaned

    @model_validator(mode="after")
    def _primary_is_observed(self):
        primary = normalize_compact_key(self.primary_surface)
        if not any(
            normalize_compact_key(surface) == primary for surface in self.observed_surfaces
        ):
            raise ValueError(
                "observed_surfaces must include the primary_surface"
            )
        return self

    @model_validator(mode="after")
    def _alias_relation_surfaces_unique(self):
        keys = [
            normalize_compact_key(relation.surface)
            for relation in self.alias_relations
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("alias_relations surfaces must be unique")
        return self


class SceneFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    fact: str = Field(min_length=1)


class SceneFactDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact: str = Field(min_length=1)


class SceneFactSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_focus: str = Field(min_length=1)
    facts: list[SceneFact] = Field(default_factory=list, max_length=12)
    npc_mentions: list[SceneNpcMention] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_fact_ids(self):
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact_id values must be unique")
        return self

    @model_validator(mode="after")
    def _npc_mention_evidence_refers_to_facts(self):
        fact_ids = {fact.fact_id for fact in self.facts}
        for mention in self.npc_mentions:
            referenced = set(mention.evidence_fact_ids)
            referenced.update(
                fact_id
                for relation in mention.alias_relations
                for fact_id in relation.evidence_fact_ids
            )
            missing = sorted(referenced - fact_ids)
            if missing:
                raise ValueError(
                    "npc_mentions reference unknown fact ids: "
                    f"{missing}"
                )
        return self

    @classmethod
    def from_drafts(
        cls,
        *,
        scene_focus: str,
        facts: list[SceneFactDraft],
        npc_mentions: list[SceneNpcMention] | None = None,
    ) -> "SceneFactSet":
        return cls(
            scene_focus=scene_focus,
            facts=[
                SceneFact(
                    fact_id=f"f{index}",
                    fact=SceneFactDraft.model_validate(fact).fact,
                )
                for index, fact in enumerate(facts, start=1)
            ],
            npc_mentions=list(npc_mentions or []),
        )


class SceneFactDraftSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_focus: str = Field(min_length=1)
    facts: list[SceneFactDraft] = Field(default_factory=list, max_length=12)


class SceneFactExtractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_text: str = Field(min_length=1)
    core_memory: CoreMemory
    current_state: CurrentState
