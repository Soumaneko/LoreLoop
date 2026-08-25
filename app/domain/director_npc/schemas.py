from functools import partial
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.ids import new_hex_id
from app.core.time import utc_now

DirectorNPCStatus = Literal["prepared", "introduced", "retired"]

# 三级长度契约（H <= A <= S）：
# - Prompt Limit H：展示给模型的质量目标；
# - Accept Limit A：第一次输出无需因该字段重试的接收上限；
# - Salvage Limit S：仅 soft_text 在重试耗尽后的最终保留上限。
PERSONALITY_TRAIT_HARD_LIMIT = 40
PERSONALITY_TRAIT_ACCEPT_LIMIT = 50
PERSONALITY_TRAIT_SOFT_LIMIT = 60
ABILITY_NAME_HARD_LIMIT = 20
ABILITY_NAME_ACCEPT_LIMIT = 25
ABILITY_DESCRIPTION_HARD_LIMIT = 120
ABILITY_DESCRIPTION_ACCEPT_LIMIT = 150
ABILITY_DESCRIPTION_SOFT_LIMIT = 180


class NPCCreationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_ids: list[str] = Field(default_factory=list, max_length=4)


class KnownAbility(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # strict_name 接收上限为 A（无截断）；description 为 soft_text 上限 S。
    name: str = Field(min_length=1, max_length=ABILITY_NAME_ACCEPT_LIMIT)
    description: str = Field(
        min_length=1,
        max_length=ABILITY_DESCRIPTION_SOFT_LIMIT,
    )


_new_id = partial(new_hex_id, prefix="director_npc", length=12)


class DirectorNPCDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    draft_id: str = Field(default_factory=_new_id)
    core_motivation: str = Field(min_length=1)
    hidden_secret: str = Field(min_length=1)
    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)
    role: str = Field(min_length=1)
    faction: str | None = None
    personality_trait: str = Field(
        min_length=1,
        max_length=PERSONALITY_TRAIT_SOFT_LIMIT,
    )
    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    appearance: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source_setting_ids: list[str] = Field(default_factory=list, max_length=4)
    status: DirectorNPCStatus = "prepared"
    created_turn: int = Field(ge=0)
    introduced_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    def private_view(self) -> "DirectorNPCPrivateProfile":
        return DirectorNPCPrivateProfile(
            draft_id=self.draft_id,
            name=self.name,
            role=self.role,
            appearance=self.appearance,
            personality_trait=self.personality_trait,
            known_abilities=list(self.known_abilities),
            faction=self.faction,
            core_motivation=self.core_motivation,
            hidden_secret=self.hidden_secret,
            conflict_style=self.conflict_style,
            social_style=self.social_style,
            source_setting_ids=list(self.source_setting_ids),
            created_turn=self.created_turn,
            introduced_turn=self.introduced_turn,
        )


class DirectorNPCIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    core_motivation: str = Field(min_length=1)
    hidden_secret: str = Field(min_length=1)
    role: str = Field(min_length=1)
    faction: str | None = None


class DirectorNPCBehavior(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)
    personality_trait: str = Field(
        min_length=1,
        max_length=PERSONALITY_TRAIT_SOFT_LIMIT,
    )


class DirectorNPCShell(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    appearance: str = Field(min_length=1)
    name: str = Field(min_length=1)


class DirectorNPCCreateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    creation_reason: str = Field(min_length=1)
    identity: DirectorNPCIdentity
    behavior: DirectorNPCBehavior
    shell: DirectorNPCShell


class _WireAbility(BaseModel):
    """宽松 wire：长度字段不设上限，由 creator 的 output_validator 分流。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class DirectorNPCBehaviorWire(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)
    personality_trait: str = Field(min_length=1)


class DirectorNPCShellWire(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    known_abilities: list[_WireAbility] = Field(
        default_factory=list,
        max_length=5,
    )
    appearance: str = Field(min_length=1)
    name: str = Field(min_length=1)


class DirectorNPCCreateOutputWire(BaseModel):
    """Gateway wire schema：允许 output_validator 看到 S 以上的值。

    Prompt 仍展示严格 ``DirectorNPCCreateOutput``（maxLength=H）。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    creation_reason: str = Field(min_length=1)
    identity: DirectorNPCIdentity
    behavior: DirectorNPCBehaviorWire
    shell: DirectorNPCShellWire


class DirectorNPCPrivateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    core_motivation: str
    hidden_secret: str
    conflict_style: str
    social_style: str
    role: str
    faction: str | None = None
    personality_trait: str
    known_abilities: list[KnownAbility] = Field(default_factory=list)
    appearance: str
    name: str
    source_setting_ids: list[str] = Field(default_factory=list)
    created_turn: int
    introduced_turn: int | None = None
