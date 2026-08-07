from app.core.text import normalize_compact_key, normalize_key


def test_normalize_key_preserves_internal_whitespace():
    assert normalize_key("  灰烬 图书馆  ") == "灰烬 图书馆"
    assert normalize_key("") == ""
    assert normalize_key(None) == ""


def test_normalize_compact_key_collapses_internal_whitespace():
    assert normalize_compact_key("  灰烬 图书馆  ") == "灰烬图书馆"
    assert normalize_compact_key("") == ""
    assert normalize_compact_key(None) == ""


def test_normalize_variants_differ_only_on_internal_whitespace():
    text = "  莱尔 · 灰烬 "
    assert normalize_key(text) == "莱尔 · 灰烬"
    assert normalize_compact_key(text) == "莱尔·灰烬"
