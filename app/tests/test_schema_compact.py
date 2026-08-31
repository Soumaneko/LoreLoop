import json

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm.schema import (
    compact_schema,
    schema_json,
)
from app.domain.current_state.changes import StateChangeSet
from app.domain.director_event.schemas import DirectorEventSelectionResult


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, description="内部值")


class _Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=20, description="姓名")
    kind: str = Field(default="x", description="类别")
    count: int = Field(ge=0, le=100)
    items: list[_Nested] = Field(
        default_factory=list,
        min_length=0,
        max_length=3,
        description="列表",
    )


def _has_title(obj) -> bool:
    if isinstance(obj, dict):
        if "title" in obj:
            return True
        return any(_has_title(value) for value in obj.values())
    if isinstance(obj, list):
        return any(_has_title(value) for value in obj)
    return False


def test_compact_schema_strips_title_metadata_only():
    compact = compact_schema(_Sample.model_json_schema())

    assert not _has_title(compact)
    assert compact["properties"]["name"]["description"] == "姓名"
    assert compact["properties"]["kind"]["default"] == "x"


def test_compact_schema_preserves_constraints():
    compact = compact_schema(_Sample.model_json_schema())
    text = json.dumps(compact, ensure_ascii=False)

    for key in (
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "default",
        "additionalProperties",
    ):
        assert f'"{key}"' in text
    assert '"description": "姓名"' in text
    assert '"type": "string"' in text
    assert '"required"' in text


def test_business_field_named_title_is_preserved():
    compact = compact_schema(DirectorEventSelectionResult.model_json_schema())

    assert "title" in compact["required"]
    assert "title" in compact["properties"]
    assert "title" not in compact["properties"]["title"]


def test_compact_schema_is_idempotent():
    once = compact_schema(_Sample.model_json_schema())
    assert compact_schema(once) == once


def test_schema_json_emits_compact_schema_for_prompt():
    rendered = schema_json(_Sample)
    assert "\n" not in rendered
    parsed = json.loads(rendered)
    assert not _has_title(parsed)


def test_state_update_schema_is_consistent():
    parsed = json.loads(schema_json(StateChangeSet))

    assert not _has_title(parsed)
    assert "operations" in parsed["properties"]
    assert set(parsed["required"]).issubset(parsed["properties"].keys())
    assert set(parsed["$defs"].keys()) == {
        "FixedAssetAdd",
        "FixedAssetRemove",
        "InventoryDelta",
        "LocationChange",
        "MoneyDelta",
        "ParticipantAdd",
        "ParticipantAttitudeChange",
        "ParticipantRemove",
        "PlayerVisibleStatusChange",
        "TemporaryAssetAdd",
        "TemporaryAssetRemove",
        "WorldClockAdvance",
    }
