from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.llm.config import (
    DEFAULT_PROTOCOL,
    PROTOCOLS,
    GatewayConfig,
    SharedGatewayFactory,
)
from app.core.llm.provider_classification import classify_provider_error
from app.core.llm.providers import LLMProvider

from .paths import APP_CONFIG_DIR
from .secret_protection import (
    SecretProtectionError,
    SecretProtector,
    default_secret_protector,
)


MODEL_CONFIG_FILE = APP_CONFIG_DIR / "model_config.json"
_PROTECTED_CONFIG_V2 = 2
MODEL_CONFIG_FORMAT_VERSION = _PROTECTED_CONFIG_V2
_CONFIG_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class StoredModelConfig:
    api_key: str
    base_url: str
    model: str
    thinking: bool | None = None
    protocol: str = DEFAULT_PROTOCOL
    # Selection metadata: which catalog entry the user picked. Display-only;
    # never validated against the directory, so delisted entries keep working.
    preset_id: str = "custom"
    model_id: str = ""

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
        protocol = value.get("protocol", DEFAULT_PROTOCOL)
        if not isinstance(protocol, str) or protocol not in PROTOCOLS:
            raise ValueError(
                f"protocol must be one of {', '.join(PROTOCOLS)}"
            )
        if protocol != "openai_chat" and thinking is True:
            raise ValueError(
                f"thinking is not supported for protocol '{protocol}' in this phase"
            )
        preset_id = value.get("preset_id", "custom")
        if not isinstance(preset_id, str):
            raise ValueError("preset_id must be a string")
        preset_id = preset_id.strip() or "custom"
        model_id = value.get("model_id", "")
        if model_id is None:
            model_id = ""
        if not isinstance(model_id, str):
            raise ValueError("model_id must be a string")
        model_id = model_id.strip()
        # Empty base_url delegates the official default to the protocol SDK.
        if not model:
            raise ValueError("Model name is required")
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            thinking=thinking,
            protocol=protocol,
            preset_id=preset_id,
            model_id=model_id,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def gateway_config(self) -> GatewayConfig:
        thinking = "enabled" if self.thinking is True else None
        return GatewayConfig(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            thinking=thinking,
            protocol=self.protocol,  # type: ignore[arg-type]
        )


class ModelConfigStore:
    def __init__(
        self,
        path: str | Path = MODEL_CONFIG_FILE,
        *,
        secret_protector: SecretProtector | None = None,
    ) -> None:
        self.path = Path(path)
        self._secret_protector = (
            default_secret_protector()
            if secret_protector is None
            else secret_protector
        )

    def load_local(self) -> StoredModelConfig | None:
        with _CONFIG_WRITE_LOCK:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Local model configuration is not valid JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError("Model configuration must be a JSON object")
            if "api_key_protected" in payload or "api_key_protection" in payload:
                return self._load_protected(payload)

            config = StoredModelConfig.from_dict(payload)
            if self._secret_protector is not None:
                # Upgrade legacy plaintext only after protection succeeds. The
                # atomic writer leaves the original file intact on failure.
                self._write_payload(self._protected_payload(config))
            return config

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
            protocol=env.protocol,
            preset_id="custom",
            model_id="",
        )

    def save(self, config: StoredModelConfig) -> None:
        with _CONFIG_WRITE_LOCK:
            payload = (
                self._protected_payload(config)
                if self._secret_protector is not None
                else asdict(config)
            )
            self._write_payload(payload)

    def public_status(self) -> dict[str, Any]:
        config = self.load_effective()
        return {
            "configured": config.configured,
            "base_url": config.base_url,
            "model": config.model,
            "thinking": config.thinking,
            "protocol": config.protocol,
            "preset_id": config.preset_id,
            "model_id": config.model_id,
        }

    def merge_payload(self, payload: dict[str, Any]) -> StoredModelConfig:
        current = self.load_effective()
        clear_key = payload.get("clear_api_key", False)
        if not isinstance(clear_key, bool):
            raise ValueError("clear_api_key must be true or false")
        supplied_key = _payload_text(payload.get("api_key"), fallback="")
        api_key = "" if clear_key else supplied_key or current.api_key
        # base_url: an explicitly supplied key (even "") wins so the
        # user can clear back to the protocol's official default.
        if "base_url" in payload:
            base_url = payload.get("base_url")
            if base_url is None:
                base_url = current.base_url
            elif not isinstance(base_url, str):
                raise ValueError("Model configuration text fields must be strings")
            else:
                base_url = base_url.strip()
        else:
            base_url = current.base_url
        return StoredModelConfig.from_dict({
            "api_key": api_key,
            "base_url": base_url,
            "model": _payload_text(
                payload.get("model"),
                fallback=current.model,
            ),
            "thinking": (
                current.thinking
                if "thinking" not in payload
                else payload["thinking"]
            ),
            "protocol": _payload_text(
                payload.get("protocol"),
                fallback=current.protocol,
            ),
            # Selection metadata only; explicit values win, omission
            # or null retains.
            "preset_id": (
                current.preset_id
                if payload.get("preset_id") is None
                else payload["preset_id"]
            ),
            "model_id": (
                current.model_id
                if payload.get("model_id") is None
                else payload["model_id"]
            ),
        })

    def _load_protected(self, payload: dict[str, Any]) -> StoredModelConfig:
        version = payload.get("format_version")
        if version == _PROTECTED_CONFIG_V2:
            return self._load_protected_v2(payload)
        raise SecretProtectionError(
            "Unsupported protected model configuration version"
        )

    def _load_protected_v2(
        self,
        payload: dict[str, Any],
    ) -> StoredModelConfig:
        """Load the v2 DPAPI/entropy-v1 contract.

        Keep this reader when introducing later formats so an upgrade can
        recover the old secret before atomically rewriting it.
        """
        protector = self._secret_protector
        if protector is None:
            raise SecretProtectionError(
                "This platform cannot recover the protected API Key"
            )
        if payload.get("api_key_protection") != protector.scheme:
            raise SecretProtectionError(
                "Unsupported API Key protection scheme"
            )
        protected = payload.get("api_key_protected")
        if not isinstance(protected, str):
            raise SecretProtectionError("Protected API Key is missing")
        api_key = protector.unprotect(protected)
        return StoredModelConfig.from_dict({
            "api_key": api_key,
            "base_url": payload.get("base_url"),
            "model": payload.get("model"),
            "thinking": payload.get("thinking"),
            "protocol": payload.get("protocol", DEFAULT_PROTOCOL),
            "preset_id": payload.get("preset_id", "custom"),
            "model_id": payload.get("model_id", ""),
        })

    def _protected_payload(self, config: StoredModelConfig) -> dict[str, Any]:
        protector = self._secret_protector
        if protector is None:
            raise SecretProtectionError("API Key protection is unavailable")
        protected = protector.protect(config.api_key)
        if protector.unprotect(protected) != config.api_key:
            raise SecretProtectionError("API Key protection verification failed")
        return {
            "format_version": MODEL_CONFIG_FORMAT_VERSION,
            "api_key_protection": protector.scheme,
            "api_key_protected": protected,
            "base_url": config.base_url,
            "model": config.model,
            "thinking": config.thinking,
            "protocol": config.protocol,
            "preset_id": config.preset_id,
            "model_id": config.model_id,
        }

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _best_effort_chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _best_effort_chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            _best_effort_chmod(self.path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)


ProviderFactory = Callable[[StoredModelConfig], LLMProvider]


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
            "protocol": config.protocol,
            "preset_id": config.preset_id,
            "model_id": config.model_id,
            "message": "连接成功",
        }
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        code, message = classify_provider_error(exc, secret=config.api_key)
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


def _connection_test_provider(config: StoredModelConfig) -> LLMProvider:
    from app.core.llm.providers import PydanticAIProvider

    return PydanticAIProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        default_model=config.model,
        thinking=config.gateway_config().thinking,
        timeout=30.0,
        protocol=config.protocol,
    )


def _failed_test(code: str, message: str, latency_ms: float) -> dict[str, Any]:
    return {
        "success": False,
        "latency_ms": round(latency_ms, 1),
        "error": {"code": code, "message": message},
    }


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
