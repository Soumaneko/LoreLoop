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

_GENERIC_PRONOUN_SURFACES = frozenset({
    "他",
    "她",
    "它",
    "他们",
    "她们",
    "它们",
    "对方",
    "那人",
    "此人",
})


def _reject_generic_pronoun(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if normalize_compact_key(clean) in _GENERIC_PRONOUN_SURFACES:
        raise ValueError(f"{field_name} must be an identity-bearing surface, not a pronoun")
    return clean


class AliasRelation(BaseModel):
    """正文表达的 alias 关系：只陈述「同一人」，不判定档案/持久化。

    ``explicit_fixed_alias`` 会被确定性持久化，因此必须携带至少一条
    ``evidence_fact_id`` 或 ``evidence_paragraph_id``（provenance 比普通提示
    字段更严格）。paragraph 引用必须来自本轮输入段落（由 extractor 的
    output_validator 校验）。
    """

    model_config = ConfigDict(extra="forbid")

    surface: str = Field(min_length=1)
    basis: NpcMentionBasis
    evidence_fact_ids: list[str] = Field(default_factory=list)
    evidence_paragraph_ids: list[str] = Field(default_factory=list)

    @field_validator("surface")
    @classmethod
    def _strip_surface(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("surface must not be blank")
        return _reject_generic_pronoun(clean, field_name="alias surface")

    @field_validator("evidence_fact_ids")
    @classmethod
    def _fact_ids_no_duplicates(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("evidence_fact_ids must not repeat")
        return values

    @field_validator("evidence_paragraph_ids")
    @classmethod
    def _paragraph_ids_no_duplicates(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("evidence_paragraph_ids must not repeat")
        return values

    @model_validator(mode="after")
    def _fixed_alias_requires_evidence(self):
        if self.basis == "explicit_fixed_alias" and not (
            self.evidence_fact_ids or self.evidence_paragraph_ids
        ):
            raise ValueError(
                "explicit_fixed_alias requires at least one "
                "evidence_fact_id or evidence_paragraph_id"
            )
        return self


class SceneNpcMention(BaseModel):
    """本章出现的一个 NPC 人物簇：主称呼 + 场景内 alias 关系。

    ``primary_surface`` 必须是姓名、稳定称号或足以区分人物的描述；普通代词不进入
    契约。其它有身份意义的称呼只通过 ``alias_relations`` 表达。
    ``evidence_fact_ids`` / ``evidence_paragraph_ids`` 为人物出现的证据。Scene Facts
    只陈述正文表达了什么，不判定 npc_id / existing / new / 是否建档 / 是否持久化。
    """

    model_config = ConfigDict(extra="forbid")

    primary_surface: str = Field(min_length=1)
    alias_relations: list[AliasRelation] = Field(default_factory=list)
    evidence_fact_ids: list[str] = Field(default_factory=list)
    evidence_paragraph_ids: list[str] = Field(default_factory=list)

    @field_validator("primary_surface")
    @classmethod
    def _strip_primary(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("primary_surface must not be blank")
        return _reject_generic_pronoun(clean, field_name="primary_surface")

    @field_validator("evidence_fact_ids")
    @classmethod
    def _fact_ids_no_duplicates(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("evidence_fact_ids must not repeat")
        return values

    @field_validator("evidence_paragraph_ids")
    @classmethod
    def _paragraph_ids_no_duplicates(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("evidence_paragraph_ids must not repeat")
        return values

    @model_validator(mode="after")
    def _requires_evidence(self):
        has_evidence = bool(
            self.evidence_fact_ids
            or self.evidence_paragraph_ids
            or any(
                relation.evidence_fact_ids or relation.evidence_paragraph_ids
                for relation in self.alias_relations
            )
        )
        if not has_evidence:
            raise ValueError(
                "npc_mentions requires at least one evidence_fact_id or "
                "evidence_paragraph_id"
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

    @model_validator(mode="after")
    def _alias_relation_surfaces_differ_from_primary(self):
        primary = normalize_compact_key(self.primary_surface)
        duplicates = sorted(
            normalize_compact_key(relation.surface)
            for relation in self.alias_relations
            if normalize_compact_key(relation.surface) == primary
        )
        if duplicates:
            raise ValueError(
                "alias_relations surface must not equal the primary_surface: "
                f"{duplicates}"
            )
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
    npc_mentions: list[SceneNpcMention] = Field(default_factory=list, max_length=12)

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
    npc_mentions: list[SceneNpcMention] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _npc_mention_fact_references_positional(self):
        # 程序按位置赋 fact_id：facts[0] -> "f1"，facts[1] -> "f2"……
        # mention/alias 的 evidence_fact_ids 必须引用 f1..fN；draft 层校验使错误
        # 引用进入 LLM 自动重试，而不是等 gateway 返回后构造 SceneFactSet 才失败。
        allowed = {f"f{index}" for index in range(1, len(self.facts) + 1)}
        for mention in self.npc_mentions:
            referenced = set(mention.evidence_fact_ids)
            referenced.update(
                fact_id
                for relation in mention.alias_relations
                for fact_id in relation.evidence_fact_ids
            )
            invalid = sorted(referenced - allowed)
            if invalid:
                raise ValueError(
                    "npc_mentions reference out-of-range fact ids "
                    f"{invalid}; facts are numbered f1..f{len(self.facts)}"
                )
        return self


class SceneParagraphInput(BaseModel):
    """本轮场景正文的带 ID 段落，供 Scene Fact LLM 引用 evidence_paragraph_ids。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class SceneFactExtractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_text: str = Field(min_length=1)
    core_memory: CoreMemory
    current_state: CurrentState
    scene_paragraphs: list[SceneParagraphInput] = Field(default_factory=list)
