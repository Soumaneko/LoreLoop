from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.llm.config import GatewayConfig, SharedGatewayFactory
from app.core.llm.providers import OpenAICompatibleProvider

from .paths import APP_CONFIG_DIR


MODEL_CONFIG_FILE = APP_CONFIG_DIR / "model_config.json"
_CONFIG_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class StoredModelConfig:
    api_key: str
    base_url: str
    model: str
    thinking: bool | None = None

    @classmethod
    def from_dict(cls, value: object) -> "StoredModelConfig":
        if not isinstance(value, dict):
            raise ValueError("Model configuration must be a JSON object")
        api_key = _stored_text(value.get("api_key"), "API Key")
        base_url = _stored_text(value.get("base_url"), "Base URL")
        model = _stored_text(value.get("model"), "Model name")
        thinking = value.get("thinking")
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError("thinking must be true, false, or null")
        if not base_url:
            raise ValueError("Base URL is required")
        if not model:
            raise ValueError("Model name is required")
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            thinking=thinking,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def gateway_config(self) -> GatewayConfig:
        thinking = (
            None
            if self.thinking is None
            else "enabled" if self.thinking else "disabled"
        )
        return GatewayConfig(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            thinking=thinking,
        )


class ModelConfigStore:
    def __init__(self, path: str | Path = MODEL_CONFIG_FILE) -> None:
        self.path = Path(path)

    def load_local(self) -> StoredModelConfig | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise ValueError("Local model configuration is not valid JSON") from exc
        return StoredModelConfig.from_dict(payload)

    def load_effective(self) -> StoredModelConfig:
        try:
            local = self.load_local()
        except ValueError:
            local = None
        if local is not None:
            return local
        env = GatewayConfig.from_env()
        return StoredModelConfig(
            api_key=env.api_key,
            base_url=env.base_url,
            model=env.model,
            thinking=(
                None if env.thinking is None else env.thinking == "enabled"
            ),
        )

    def save(self, config: StoredModelConfig) -> None:
        with _CONFIG_WRITE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _best_effort_chmod(self.path.parent, 0o700)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            try:
                temporary.write_text(
                    json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                _best_effort_chmod(temporary, 0o600)
                os.replace(temporary, self.path)
                _best_effort_chmod(self.path, 0o600)
            finally:
                temporary.unlink(missing_ok=True)

    def public_status(self) -> dict[str, Any]:
        config = self.load_effective()
        return {
            "configured": config.configured,
            "base_url": config.base_url,
            "model": config.model,
            "thinking": config.thinking,
        }

    def merge_payload(self, payload: dict[str, Any]) -> StoredModelConfig:
        current = self.load_effective()
        clear_key = payload.get("clear_api_key", False)
        if not isinstance(clear_key, bool):
            raise ValueError("clear_api_key must be true or false")
        supplied_key = _payload_text(payload.get("api_key"), fallback="")
        api_key = "" if clear_key else supplied_key or current.api_key
        return StoredModelConfig.from_dict({
            "api_key": api_key,
            "base_url": _payload_text(
                payload.get("base_url"),
                fallback=current.base_url,
            ),
            "model": _payload_text(
                payload.get("model"),
                fallback=current.model,
            ),
            "thinking": (
                current.thinking
                if "thinking" not in payload
                else payload["thinking"]
            ),
        })


ProviderFactory = Callable[[StoredModelConfig], OpenAICompatibleProvider]


def model_config_status(*, store: ModelConfigStore | None = None) -> dict[str, Any]:
    return (store or ModelConfigStore()).public_status()


def test_model_connection(
    payload: dict[str, Any],
    *,
    store: ModelConfigStore | None = None,
    provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    store = store or ModelConfigStore()
    config = store.merge_payload(payload)
    if not config.api_key:
        return _failed_test("api_key_missing", "请先填写 API Key", 0.0)
    factory = provider_factory or _connection_test_provider
    started = time.perf_counter()
    try:
        result = factory(config).generate(
            "Reply with a short confirmation that the connection works.",
            model=config.model,
            temperature=0.0,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        if not result.content.strip():
            return _failed_test(
                "empty_response",
                "模型连接成功，但返回了空响应",
                latency_ms,
            )
        return {
            "success": True,
            "latency_ms": round(latency_ms, 1),
            "model": config.model,
            "thinking": config.thinking,
            "message": "连接成功",
        }
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        code, message = _classify_connection_error(exc, secret=config.api_key)
        return _failed_test(code, message, latency_ms)


def save_model_config(
    payload: dict[str, Any],
    *,
    store: ModelConfigStore | None = None,
    runtime=None,
) -> dict[str, Any]:
    store = store or ModelConfigStore()
    with _CONFIG_WRITE_LOCK:
        config = store.merge_payload(payload)
        if not config.api_key:
            raise ValueError("API Key is required")
        runtime = runtime or _campaign_runtime()
        factory = SharedGatewayFactory(config=config.gateway_config())
        runtime.replace_gateway_factory(
            factory,
            persist=lambda: store.save(config),
        )
        return store.public_status()


def effective_gateway_factory(
    *,
    store: ModelConfigStore | None = None,
) -> SharedGatewayFactory:
    config = (store or ModelConfigStore()).load_effective()
    return SharedGatewayFactory(config=config.gateway_config())


def _connection_test_provider(config: StoredModelConfig) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        default_model=config.model,
        thinking=config.gateway_config().thinking,
        timeout=30.0,
    )


def _failed_test(code: str, message: str, latency_ms: float) -> dict[str, Any]:
    return {
        "success": False,
        "latency_ms": round(latency_ms, 1),
        "error": {"code": code, "message": message},
    }


def _classify_connection_error(
    exc: Exception,
    *,
    secret: str = "",
) -> tuple[str, str]:
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


def _stored_text(value: object, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value.strip()


def _payload_text(value: object, *, fallback: str) -> str:
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise ValueError("Model configuration text fields must be strings")
    return value.strip() or fallback


def _best_effort_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def _campaign_runtime():
    from .session import get_campaign_runtime

    return get_campaign_runtime()
