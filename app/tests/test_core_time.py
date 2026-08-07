from datetime import datetime, timezone

from app.core.time import utc_datetime_now, utc_now


def test_utc_now_returns_isoformat_string_with_timezone():
    value = utc_now()
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(None) == timezone.utc.utcoffset(None)


def test_utc_datetime_now_returns_aware_utc_datetime():
    value = utc_datetime_now()
    assert isinstance(value, datetime)
    assert value.tzinfo is not None
    assert value.tzinfo.utcoffset(None) == timezone.utc.utcoffset(None)
