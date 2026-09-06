from __future__ import annotations

import json
import os
import threading
import time
import uuid
from urllib.parse import urlsplit
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
MODEL_CONFIG_FORMAT_VERSION = 3
_CONFIG_WRITE_LOCK = threading.RLock()

CUSTOM_PRESET = "custom"
DEFAULT_CUSTOM_NAME = "自定义服务"


@dataclass(frozen=True)
class StoredModelConfig:
    """A single saved service (one per catalog provider, or one Custom entry)."""

    config_id: str = ""
    name: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    thinking: bool | None = None
    protocol: str = DEFAULT_PROTOCOL
    preset_id: str = CUSTOM_PRESET
    model_id: str = ""

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


@dataclass(frozen=True)
class ModelConfigSet:
    services: tuple[StoredModelConfig, ...]
    active_config_id: str

    def active(self) -> StoredModelConfig | None:
        """The service matching ``active_config_id``, else None.

        Never falls back to the first service: a service that was saved but
        never enabled must not silently become the active one (running,
        restarted, and displayed config would diverge).
        """
        for service in self.services:
            if service.config_id == self.active_config_id:
                return service
        return None

    def find(self, config_id: str) -> StoredModelConfig | None:
        for service in self.services:
            if service.config_id == config_id:
                return service
        return None

    def upsert(self, config: StoredModelConfig) -> "ModelConfigSet":
        replaced = False
        services = []
        for service in self.services:
            if service.config_id == config.config_id:
                services.append(config)
                replaced = True
            else:
                services.append(service)
        if not replaced:
            services.append(config)
        active = (
            config.config_id
            if self.active_config_id == config.config_id
            else self.active_config_id
        )
        return ModelConfigSet(tuple(services), active)

    def set_active(self, config_id: str) -> "ModelConfigSet":
        return ModelConfigSet(self.services, config_id)

    def without(self, config_id: str) -> "ModelConfigSet":
        services = tuple(
            service for service in self.services if service.config_id != config_id
        )
        active = self.active_config_id
        if active == config_id:
            active = services[0].config_id if services else ""
        return ModelConfigSet(services, active)


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

    def load_set(self) -> ModelConfigSet:
        with _CONFIG_WRITE_LOCK:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return ModelConfigSet((), "")
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Local model configuration is not valid JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError("Model configuration must be a JSON object")
            version = payload.get("format_version")
            if version == MODEL_CONFIG_FORMAT_VERSION:
                return self._load_v3(payload)
            if version == _PROTECTED_CONFIG_V2 or (
                version is None and "services" not in payload
            ):
                return self._migrate_v2(payload)
            raise SecretProtectionError(
                "Unsupported model configuration version"
            )

    def save_set(self, config_set: ModelConfigSet) -> None:
        with _CONFIG_WRITE_LOCK:
            self._write_payload(self._payload_for_set(config_set))

    def load_effective(self) -> StoredModelConfig:
        """Active service, or the environment bootstrap when empty."""
        try:
            config_set = self.load_set()
        except ValueError:
            config_set = ModelConfigSet((), "")
        active = config_set.active()
        if active is not None:
            return active
        env = GatewayConfig.from_env()
        return StoredModelConfig(
            config_id="",
            name="",
            api_key=env.api_key,
            base_url=env.base_url,
            model=env.model,
            thinking=(
                None if env.thinking is None else env.thinking == "enabled"
            ),
            protocol=env.protocol,
            preset_id=CUSTOM_PRESET,
            model_id="",
        )

    def resolve_draft(
        self,
        payload: dict[str, Any],
        config_set: ModelConfigSet,
    ) -> StoredModelConfig:
        """Resolve a create/edit draft under key-ownership rules.

        New services must supply their own Key and never inherit the
        active one. Editing an existing service may reuse its stored Key
        only when the provider identity and effective endpoint are
        unchanged; a provider or endpoint change requires a fresh Key.
        """
        config_id = str(payload.get("config_id") or "").strip()
        if config_id and config_set.find(config_id) is None:
            raise ValueError("未知的服务配置")
        existing = config_set.find(config_id) if config_id else None
        is_new = existing is None

        preset_id = _payload_text(
            payload.get("preset_id"), fallback=CUSTOM_PRESET
        )
        protocol = _payload_text(
            payload.get("protocol"), fallback=DEFAULT_PROTOCOL
        )
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol must be one of {', '.join(PROTOCOLS)}")
        base_url = _stored_text(payload.get("base_url"), "Base URL")
        model = _stored_text(payload.get("model"), "Model name")
        model_id = _stored_text(payload.get("model_id"), "Model id")
        thinking = payload.get("thinking")
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError("thinking must be true, false, or null")
        if protocol != "openai_chat" and thinking is True:
            raise ValueError(
                f"thinking is not supported for protocol '{protocol}' in this phase"
            )
        if not model:
            raise ValueError("Model name is required")

        candidate_key = _stored_text(payload.get("api_key"), "API Key")

        if is_new:
            if not candidate_key:
                raise ValueError("此服务需要填写 API Key")
            if preset_id != CUSTOM_PRESET:
                for service in config_set.services:
                    if service.preset_id == preset_id:
                        raise ValueError(
                            f"已存在使用此 Provider 的服务：{service.name or service.preset_id}"
                        )
            config_id = uuid.uuid4().hex
            name = _service_name(payload, preset_id)
            return StoredModelConfig(
                config_id=config_id,
                name=name,
                api_key=candidate_key,
                base_url=base_url,
                model=model,
                thinking=thinking,
                protocol=protocol,
                preset_id=preset_id,
                model_id=model_id,
            )

        # Edit: provider identity is fixed; a change is rejected, not "turned
        # into" another provider (prevents uniqueness/key-ownership bypass).
        if existing.preset_id != preset_id:
            raise ValueError("编辑服务不能更改 Provider 身份，请添加新服务")
        if existing.protocol != protocol or existing.base_url != base_url:
            if not candidate_key:
                raise ValueError(
                    "服务已改变，请填写此服务的 API Key"
                )
        api_key = candidate_key or existing.api_key
        name = existing.name
        if preset_id == CUSTOM_PRESET:
            supplied_name = _stored_text(payload.get("name"), "名称")
            if supplied_name:
                name = supplied_name
        return StoredModelConfig(
            config_id=existing.config_id,
            name=name,
            api_key=api_key,
            base_url=base_url,
            model=model,
            thinking=thinking,
            protocol=protocol,
            preset_id=preset_id,
            model_id=model_id,
        )

    def _load_v3(self, payload: dict[str, Any]) -> ModelConfigSet:
        services_raw = payload.get("services")
        if not isinstance(services_raw, list):
            raise ValueError("Model configuration 'services' must be a list")
        services = tuple(
            self._service_from_payload(item) for item in services_raw
        )
        active = str(payload.get("active_config_id") or "")
        return ModelConfigSet(services, active)

    def _service_from_payload(self, item: Any) -> StoredModelConfig:
        if not isinstance(item, dict):
            raise ValueError("Each service must be a JSON object")
        if "api_key_protected" in item:
            api_key = self._decrypt(item["api_key_protected"])
        else:
            api_key = _stored_text(item.get("api_key"), "API Key")
        protocol = item.get("protocol", DEFAULT_PROTOCOL)
        if protocol not in PROTOCOLS:
            raise ValueError(
                f"protocol must be one of {', '.join(PROTOCOLS)}"
            )
        thinking = item.get("thinking")
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError("thinking must be true, false, or null")
        return StoredModelConfig(
            config_id=str(item.get("config_id") or ""),
            name=str(item.get("name") or ""),
            api_key=api_key,
            base_url=str(item.get("base_url") or ""),
            model=str(item.get("model") or ""),
            thinking=thinking,
            protocol=protocol,
            preset_id=str(item.get("preset_id") or CUSTOM_PRESET),
            model_id=str(item.get("model_id") or ""),
        )

    def _migrate_v2(self, payload: dict[str, Any]) -> ModelConfigSet:
        """Migrate a single-config v2 file into a one-service v3 set.

        Idempotent; never rewrites on failure. The migrated service keeps
        its identity: a non-custom preset_id is retained (shown by id when
        the catalog is unavailable), otherwise it becomes a Custom entry
        named "自定义服务". No directory/network lookup.
        """
        if "api_key_protected" in payload or "api_key_protection" in payload:
            api_key = self._decrypt(payload.get("api_key_protected") or "")
        else:
            api_key = _stored_text(payload.get("api_key"), "API Key")
        preset_id = str(payload.get("preset_id") or CUSTOM_PRESET)
        protocol = str(payload.get("protocol") or DEFAULT_PROTOCOL)
        if protocol not in PROTOCOLS:
            raise ValueError(
                f"protocol must be one of {', '.join(PROTOCOLS)}"
            )
        thinking = payload.get("thinking")
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError("thinking must be true, false, or null")
        name = (
            str(payload.get("name") or preset_id)
            if preset_id != CUSTOM_PRESET
            else DEFAULT_CUSTOM_NAME
        )
        service = StoredModelConfig(
            config_id=uuid.uuid4().hex,
            name=name,
            api_key=api_key,
            base_url=str(payload.get("base_url") or ""),
            model=str(payload.get("model") or ""),
            thinking=thinking,
            protocol=protocol,
            preset_id=preset_id,
            model_id=str(payload.get("model_id") or ""),
        )
        config_set = ModelConfigSet((service,), service.config_id)
        self.save_set(config_set)
        return config_set

    def _payload_for_set(self, config_set: ModelConfigSet) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format_version": MODEL_CONFIG_FORMAT_VERSION,
            "active_config_id": config_set.active_config_id,
            "services": [
                self._service_to_payload(service)
                for service in config_set.services
            ],
        }
        if self._secret_protector is not None:
            payload["api_key_protection"] = self._secret_protector.scheme
        return payload

    def _service_to_payload(self, config: StoredModelConfig) -> dict[str, Any]:
        base: dict[str, Any] = {
            "config_id": config.config_id,
            "name": config.name,
            "base_url": config.base_url,
            "model": config.model,
            "thinking": config.thinking,
            "protocol": config.protocol,
            "preset_id": config.preset_id,
            "model_id": config.model_id,
        }
        if self._secret_protector is not None:
            base["api_key_protected"] = self._protect(config.api_key)
        else:
            base["api_key"] = config.api_key
        return base

    def _protect(self, secret: str) -> str:
        protector = self._secret_protector
        if protector is None:
            raise SecretProtectionError("API Key protection is unavailable")
        protected = protector.protect(secret)
        if protector.unprotect(protected) != secret:
            raise SecretProtectionError("API Key protection verification failed")
        return protected

    def _decrypt(self, protected: str) -> str:
        protector = self._secret_protector
        if protector is None:
            raise SecretProtectionError(
                "This platform cannot recover the protected API Key"
            )
        if not isinstance(protected, str):
            raise SecretProtectionError("Protected API Key is missing")
        return protector.unprotect(protected)

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
    """Read-only projection of the currently active service."""
    store = store or ModelConfigStore()
    config = store.load_effective()
    return {
        "configured": config.configured,
        "base_url": config.base_url,
        "model": config.model,
        "thinking": config.thinking,
        "protocol": config.protocol,
        "preset_id": config.preset_id,
        "model_id": config.model_id,
    }


def service_display_name(service: StoredModelConfig) -> str:
    if service.preset_id != CUSTOM_PRESET:
        return service.name or service.preset_id
    try:
        host = urlsplit(service.base_url).hostname
    except ValueError:
        host = None
    target = host or f"官方默认 · {service.protocol}"
    return f"{target} · {service.model}"


def list_model_configs(*, store: ModelConfigStore | None = None) -> dict[str, Any]:
    store = store or ModelConfigStore()
    config_set = store.load_set()
    services = []
    for service in config_set.services:
        services.append({
            "config_id": service.config_id,
            "name": service_display_name(service),
            "model": service.model,
            "preset_id": service.preset_id,
            "model_id": service.model_id,
            "protocol": service.protocol,
            "base_url": service.base_url,
            "thinking": service.thinking,
            "has_api_key": bool(service.api_key),
            "active": service.config_id == config_set.active_config_id,
        })
    return {
        "services": services,
        "active_config_id": config_set.active_config_id,
    }


def save_model_config(
    payload: dict[str, Any],
    *,
    store: ModelConfigStore | None = None,
    runtime=None,
) -> dict[str, Any]:
    """Create or edit a service; optionally save-and-enable."""
    store = store or ModelConfigStore()
    with _CONFIG_WRITE_LOCK:
        config_set = store.load_set()
        config = store.resolve_draft(payload, config_set)
        if not config.api_key:
            raise ValueError("此服务需要填写 API Key")
        new_set = config_set.upsert(config)
        activate = bool(payload.get("activate", False))
        is_active = config_set.active_config_id == config.config_id
        if activate:
            new_set = new_set.set_active(config.config_id)
        if activate or is_active:
            runtime = runtime or _campaign_runtime()
            factory = SharedGatewayFactory(config=config.gateway_config())
            runtime.replace_gateway_factory(
                factory,
                persist=lambda: store.save_set(new_set),
            )
        else:
            store.save_set(new_set)
    return list_model_configs(store=store)


def activate_model_config(
    config_id: str,
    *,
    store: ModelConfigStore | None = None,
    runtime=None,
) -> dict[str, Any]:
    store = store or ModelConfigStore()
    with _CONFIG_WRITE_LOCK:
        config_set = store.load_set()
        service = config_set.find(config_id)
        if service is None:
            raise ValueError("未知的服务配置")
        if not service.api_key:
            raise ValueError("此服务缺少 API Key，请先编辑并填写")
        new_set = config_set.set_active(config_id)
        runtime = runtime or _campaign_runtime()
        factory = SharedGatewayFactory(config=service.gateway_config())
        runtime.replace_gateway_factory(
            factory,
            persist=lambda: store.save_set(new_set),
        )
    return list_model_configs(store=store)


def delete_model_config(
    config_id: str,
    *,
    store: ModelConfigStore | None = None,
    runtime=None,
) -> dict[str, Any]:
    store = store or ModelConfigStore()
    with _CONFIG_WRITE_LOCK:
        config_set = store.load_set()
        if config_set.find(config_id) is None:
            raise ValueError("未知的服务配置")
        if config_set.active_config_id == config_id:
            raise ValueError("不能删除当前正在使用的服务，请先启用另一份")
        new_set = config_set.without(config_id)
        store.save_set(new_set)
    return list_model_configs(store=store)


def test_model_connection(
    payload: dict[str, Any],
    *,
    store: ModelConfigStore | None = None,
    provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    """Test a draft without saving or activating anything."""
    store = store or ModelConfigStore()
    with _CONFIG_WRITE_LOCK:
        config_set = store.load_set()
        try:
            config = store.resolve_draft(payload, config_set)
        except ValueError as exc:
            return _failed_test("api_key_missing", str(exc), 0.0)
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


def _service_name(payload: dict[str, Any], preset_id: str) -> str:
    name = _stored_text(payload.get("name"), "名称")
    if preset_id == CUSTOM_PRESET:
        # Legacy names remain readable; the UI derives Custom display labels.
        # never silently substitute a generic placeholder.
        return name or DEFAULT_CUSTOM_NAME
    return name or preset_id


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
