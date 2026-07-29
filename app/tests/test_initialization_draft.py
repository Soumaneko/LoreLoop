import pytest

from app.domain.core_memory import CoreMemory
from app.runtime.initialization_draft import (
    InitializationDraft,
    clear_initialization_draft,
    load_initialization_draft,
    save_initialization_draft,
)
from app.runtime.session import get_campaign_runtime


def _draft():
    return {
        "version": 1,
        "stage": "story_direction",
        "form": {
            "fanfic": False,
            "fields": {"world_name": "艾兰"},
        },
        "config": {"world_name": "艾兰"},
        "story_direction": {"content": "向北探索"},
    }


def test_initialization_draft_round_trip_and_clear():
    saved = save_initialization_draft(_draft())

    assert saved == InitializationDraft.model_validate(_draft()).model_dump(
        mode="json"
    )
    assert load_initialization_draft() == saved

    clear_initialization_draft()

    assert load_initialization_draft() is None


def test_initialization_draft_rejects_unknown_contract_fields():
    with pytest.raises(ValueError):
        save_initialization_draft({**_draft(), "legacy_stage": "old"})


def test_initialization_draft_cannot_be_written_after_initialization():
    get_campaign_runtime().stores.core_memory().save(CoreMemory(items=[]))

    with pytest.raises(RuntimeError, match="already complete"):
        save_initialization_draft(_draft())
