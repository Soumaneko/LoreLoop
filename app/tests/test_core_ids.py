import re

from app.core.ids import new_hex_id


def test_new_hex_id_defaults_to_full_hex():
    value = new_hex_id()
    assert re.fullmatch(r"[0-9a-f]{32}", value)


def test_new_hex_id_supports_prefix_and_truncation():
    value = new_hex_id(prefix="director_event", length=12)
    assert re.fullmatch(r"director_event_[0-9a-f]{12}", value)


def test_new_hex_id_truncates_suffix_to_requested_length():
    long_suffix = new_hex_id(length=20)
    assert re.fullmatch(r"[0-9a-f]{20}", long_suffix)
