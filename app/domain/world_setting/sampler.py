from __future__ import annotations

import random

from .schemas import WorldSetting
from .store import WorldSettingStore


def reserve_setting_candidates(
    store: WorldSettingStore,
    *,
    owner: str,
    count: int,
    rng: random.Random | None = None,
) -> list[WorldSetting]:
    if count <= 0:
        return []
    existing = store.list_reserved_by(owner)
    if len(existing) >= count:
        return existing[:count]
    reserved_ids = {setting.setting_id for setting in existing}
    available = [
        setting
        for setting in store.list_available()
        if setting.setting_id not in reserved_ids
    ]
    random_source = rng or random.SystemRandom()
    random_source.shuffle(available)
    needed = count - len(existing)
    newly_reserved = store.reserve(
        [setting.setting_id for setting in available[:needed]],
        owner=owner,
    )
    return [*existing, *newly_reserved]
