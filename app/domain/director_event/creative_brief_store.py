import json
import sqlite3

from app.runtime.sqlite import ensure_exact_schema_version, transaction

from .creative_brief import EVENT_CREATIVE_POOLS, EventCreativeDecks


# Freeze the original labels so editing the current copy cannot break old saves.
_LEGACY_CARD_IDS = {
    "隐藏已久的秘密或旧账被翻出": "cause_old_secret",
    "突发变故或意外灾祸打破了日常": "cause_disaster",
    "某项截止期限或关键节点即将到来": "cause_deadline",
    "无意中发现了禁忌真相或高价值之物": "cause_discovery",
    "维持现状的资源或渠道出现短缺": "cause_shortage",
    "长期积累的内部矛盾终于公开爆发": "cause_internal_conflict",
    "亲近者或同伴": "opposition_companion",
    "竞争者或同行": "opposition_rival",
    "既有组织或权威": "opposition_authority",
    "利益受损者": "opposition_affected_party",
    "打破秩序的外敌": "opposition_invader",
    "不明真相的群体": "opposition_uninformed_group",
    "紧张急迫": "tone_urgent",
    "暗流涌动": "tone_understated_tension",
    "悬疑诡谲": "tone_eerie",
    "荒诞乌龙": "tone_farce",
    "压抑沉重": "tone_grim",
    "热闹喧嚣": "tone_lively",
    "剑拔弩张": "tone_confrontational",
    "悲喜交加": "tone_bittersweet",
}


class EventCreativeDeckStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS event_creative_decks "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), decks_json TEXT NOT NULL)"
        )
        ensure_exact_schema_version(conn, "event_creative_decks", 1)
        conn.commit()

    def load(self) -> EventCreativeDecks:
        row = self._conn.execute(
            "SELECT decks_json FROM event_creative_decks WHERE id = 1"
        ).fetchone()
        if row is None:
            return EventCreativeDecks()
        saved = json.loads(row[0])
        if isinstance(saved, dict):
            for key, pool in EVENT_CREATIVE_POOLS.items():
                cards = saved.get(key)
                if isinstance(cards, list) and all(isinstance(card, str) for card in cards):
                    card_ids = (_LEGACY_CARD_IDS.get(card, card) for card in cards)
                    saved[key] = [card_id for card_id in card_ids if card_id in pool]
        return EventCreativeDecks.model_validate(saved)

    def save(self, decks: EventCreativeDecks) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO event_creative_decks (id, decks_json) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET decks_json = excluded.decks_json",
                (decks.model_dump_json(),),
            )
