import pytest

from app.core.llm.provider_classification import (
    PROVIDER_ERROR_PERMANENT_CODES,
    classify_provider_error,
    is_permanent_provider_error,
)


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        ("Authentication failed: 401", "auth_failed"),
        ("403 Forbidden", "permission_denied"),
        ("Model not found: 404", "model_not_found"),
        ("Rate limit exceeded: 429", "rate_limited"),
        ("Request timed out", "timeout"),
        ("Connection error: DNS resolution failed", "endpoint_unreachable"),
        ("thinking mode unsupported", "thinking_unsupported"),
        ("random provider failure", "provider_error"),
    ],
)
def test_classify_provider_error_uses_existing_codes(message, expected_code):
    code, _ = classify_provider_error(RuntimeError(message))
    assert code == expected_code


def test_permanent_provider_error_codes_are_never_retried():
    assert PROVIDER_ERROR_PERMANENT_CODES == {
        "auth_failed",
        "permission_denied",
        "model_not_found",
        "thinking_unsupported",
    }
    for code in PROVIDER_ERROR_PERMANENT_CODES:
        assert is_permanent_provider_error(code)
    for code in (
        "rate_limited",
        "timeout",
        "endpoint_unreachable",
        "provider_error",
    ):
        assert not is_permanent_provider_error(code)
