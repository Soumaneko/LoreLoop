from pydantic import BaseModel, ConfigDict, Field


class SkillEventSelectionArguments(BaseModel):
    """Test-only stand-in for the present-event skill's arguments.

    Not a production contract: kept separate from ``skill_specs`` so test
    schemas never shadow the real Director skill argument models.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)


class SkillCreateEventArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)


class SkillCreateNPCArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
