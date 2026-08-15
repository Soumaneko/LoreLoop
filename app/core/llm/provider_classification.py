from __future__ import annotations

#: Provider error codes that cannot recover by retrying the same request.
PROVIDER_ERROR_PERMANENT_CODES = frozenset({
    "auth_failed",
    "permission_denied",
    "model_not_found",
    "thinking_unsupported",
})


def classify_provider_error(
    exc: Exception,
    *,
    secret: str = "",
) -> tuple[str, str]:
    """Classify a provider exception into a stable (code, message) pair.

    The classification is text-based to keep it usable across provider SDK
    versions and both the connection test and the formal Gateway retry path.
    """
    text = _safe_error_text(exc, secret=secret)
    lowered = text.casefold()
    if any(token in lowered for token in ("401", "unauthorized", "authentication")):
        return "auth_failed", "API Key 无效或没有访问权限"
    if "403" in lowered or "forbidden" in lowered:
        return "permission_denied", "API Key 有效，但没有访问当前服务或模型的权限"
    if any(token in lowered for token in ("404", "model not found", "does not exist")):
        return "model_not_found", "服务可访问，但没有找到指定模型"
    if "429" in lowered or "rate limit" in lowered:
        return "rate_limited", "服务当前触发了速率限制"
    if "thinking" in lowered and any(
        token in lowered for token in ("unsupported", "unknown", "invalid")
    ):
        return "thinking_unsupported", "当前服务或模型不支持所选 Thinking 设置"
    if any(token in lowered for token in ("timeout", "timed out")):
        return "timeout", "连接模型服务超时"
    if any(token in lowered for token in ("connection", "connect", "dns", "name resolution")):
        return "endpoint_unreachable", "无法连接 Base URL，请检查地址和网络"
    return "provider_error", text or "模型服务返回了未知错误"


def is_permanent_provider_error(code: str) -> bool:
    return code in PROVIDER_ERROR_PERMANENT_CODES


def _safe_error_text(exc: Exception, *, secret: str = "") -> str:
    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(messages) < 3:
        message = str(current).strip()
        if message and message not in messages:
            messages.append(message)
        current = current.__cause__
    text = ": ".join(messages)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return text[:500]
