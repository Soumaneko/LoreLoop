import json
from contextlib import closing
from copy import deepcopy

from app.domain.director_event.creative_brief import EVENT_CREATIVE_POOLS, EventCreativeDecks, draw_event_creative_brief
from app.domain.director_event.creative_brief_store import EventCreativeDeckStore
from app.runtime.sqlite import connect_sqlite


def test_independent_decks_shuffle_only_on_demand():
    class Shuffler:
        def __init__(self):
            self.calls = 0

        def shuffle(self, cards):
            self.calls += 1
            if self.calls % 2:
                cards.reverse()

    rng = Shuffler()
    decks = EventCreativeDecks()
    seen = {key: [] for key in EVENT_CREATIVE_POOLS}
    for index in range(6):
        before = deepcopy(decks)
        brief, next_decks = draw_event_creative_brief(decks, rng=rng)
        assert decks == before
        for key in seen:
            seen[key].append(brief[key])
        decks = next_decks
        if index == 4:
            assert decks.tone == []
            assert rng.calls == 3
    assert decks.cause == decks.opposition == []
    assert rng.calls == 4
    for key, pool in EVENT_CREATIVE_POOLS.items():
        assert set(seen[key][:len(pool)]) == set(pool.values())
    draw_event_creative_brief(decks, rng=rng)
    assert rng.calls == 6

    empty = EventCreativeDecks()
    first, _ = draw_event_creative_brief(empty, rng=rng)
    second, _ = draw_event_creative_brief(empty, rng=rng)
    assert first != second


def test_saved_deck_survives_renamed_and_retired_cards(monkeypatch):
    with closing(connect_sqlite(":memory:")) as conn:
        store = EventCreativeDeckStore(conn)
        conn.execute(
            "INSERT INTO event_creative_decks VALUES (1, ?)",
            (json.dumps({
                "cause": ["隐藏已久的秘密或旧账被翻出",
                          "cause_deadline", "维持现状的资源或渠道出现短缺"],
                "tone": ["tone_confrontational", "暗流涌动", "tone_urgent", "tone_eerie"],
            }),),
        )
        monkeypatch.setitem(EVENT_CREATIVE_POOLS["cause"], "cause_old_secret", "一桩旧事重新浮出水面")
        brief, remaining = draw_event_creative_brief(store.load())
        assert brief["cause"] == "一桩旧事重新浮出水面"
        assert brief["tone"] == "紧张急迫"
        store.save(remaining)
        raw = conn.execute("SELECT decks_json FROM event_creative_decks").fetchone()[0]
        assert json.loads(raw)["cause"] == ["cause_deadline", "cause_shortage"]
        assert json.loads(raw)["tone"] == ["tone_eerie"]
