import json
import os
import stat
import sys
import threading

import pytest

from app.core.llm.config import SharedGatewayFactory
from app.core.llm.providers import LLMProvider, ProviderResult
from app.runtime.model_config import (
    MODEL_CONFIG_FORMAT_VERSION,
    ModelConfigStore,
    StoredModelConfig,
    effective_gateway_factory,
    save_model_config,
    test_model_connection as check_model_connection,
)
from app.runtime.paths import CampaignPaths
from app.runtime.secret_protection import DPAPICurrentUserProtector
from app.runtime.session import CampaignRuntime


class _ReversibleProtector:
    scheme = "test-current-user"

    def protect(self, secret):
        return f"protected:{secret[::-1]}"

    def unprotect(self, protected):
        if not protected.startswith("protected:"):
            raise RuntimeError("invalid protected value")
        return protected.removeprefix("protected:")[::-1]


def _payload(**overrides):
    return {
        "api_key": "local-secret",
        "base_url": "https://example.test/v1",
        "model": "story-model",
        "thinking": True,
        "protocol": "openai_chat",
        "preset_id": "custom",
        "model_id": "",
        **overrides,
    }


def test_local_model_config_overrides_environment_and_never_exposes_key(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save(StoredModelConfig.from_dict(_payload()))

    effective = store.load_effective()
    status = store.public_status()

    assert effective.api_key == "local-secret"
    assert status == {
        "configured": True,
        "base_url": "https://example.test/v1",
        "model": "story-model",
        "thinking": True,
        "protocol": "openai_chat",
        "preset_id": "custom",
        "model_id": "",
    }
    assert "api_key" not in status
    assert "local-secret" not in json.dumps(status)
    persisted = store.path.read_text(encoding="utf-8")
    if sys.platform == "win32":
        assert "local-secret" not in persisted
        assert '"api_key_protection": "dpapi-current-user"' in persisted
    else:
        assert '"api_key": "local-secret"' in persisted
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_windows_dpapi_current_user_round_trip():
    protector = DPAPICurrentUserProtector()

    protected = protector.protect("测试-api-key")

    assert protected
    assert "测试-api-key" not in protected
    assert protector.unprotect(protected) == "测试-api-key"


def test_protected_store_never_writes_plaintext_api_key(tmp_path):
    path = tmp_path / "model_config.json"
    store = ModelConfigStore(
        path,
        secret_protector=_ReversibleProtector(),
    )

    store.save(StoredModelConfig.from_dict(_payload()))

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == {
        "format_version": MODEL_CONFIG_FORMAT_VERSION,
        "api_key_protection": "test-current-user",
        "api_key_protected": "protected:terces-lacol",
        "base_url": "https://example.test/v1",
        "model": "story-model",
        "thinking": True,
        "protocol": "openai_chat",
        "preset_id": "custom",
        "model_id": "",
    }
    assert "local-secret" not in path.read_text(encoding="utf-8")
    assert store.load_local().api_key == "local-secret"


def test_protected_store_atomically_migrates_legacy_plaintext(tmp_path):
    path = tmp_path / "model_config.json"
    path.write_text(
        json.dumps(_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    store = ModelConfigStore(
        path,
        secret_protector=_ReversibleProtector(),
    )

    loaded = store.load_local()

    assert loaded.api_key == "local-secret"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in persisted
    assert persisted["api_key_protection"] == "test-current-user"
    assert persisted["api_key_protected"] == "protected:terces-lacol"


def test_failed_plaintext_migration_preserves_original_file(tmp_path):
    class BrokenProtector(_ReversibleProtector):
        def protect(self, _secret):
            raise RuntimeError("protection failed")

    path = tmp_path / "model_config.json"
    original = json.dumps(_payload(), ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    store = ModelConfigStore(path, secret_protector=BrokenProtector())

    with pytest.raises(RuntimeError, match="protection failed"):
        store.load_local()

    assert path.read_text(encoding="utf-8") == original


def test_protected_config_does_not_fall_back_when_secret_cannot_be_recovered(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "model_config.json"
    path.write_text(
        json.dumps({
            "format_version": MODEL_CONFIG_FORMAT_VERSION,
            "api_key_protection": "other-scheme",
            "api_key_protected": "ciphertext",
            "base_url": "https://example.test/v1",
            "model": "story-model",
            "thinking": False,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    store = ModelConfigStore(
        path,
        secret_protector=_ReversibleProtector(),
    )

    with pytest.raises(RuntimeError, match="protection scheme"):
        store.load_effective()


def test_unsupported_protected_format_is_preserved_for_future_migration(
    tmp_path,
):
    path = tmp_path / "model_config.json"
    original = json.dumps({
        "format_version": MODEL_CONFIG_FORMAT_VERSION + 1,
        "api_key_protection": "test-current-user",
        "api_key_protected": "protected:terces-lacol",
        "base_url": "https://example.test/v1",
        "model": "story-model",
        "thinking": False,
    })
    path.write_text(original, encoding="utf-8")
    store = ModelConfigStore(
        path,
        secret_protector=_ReversibleProtector(),
    )

    with pytest.raises(RuntimeError, match="configuration version"):
        store.load_effective()

    assert path.read_text(encoding="utf-8") == original


def test_environment_is_bootstrap_when_no_local_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_THINKING", "enabled")

    factory = effective_gateway_factory(
        store=ModelConfigStore(tmp_path / "missing.json")
    )

    assert factory.config.api_key == "environment-secret"
    assert factory.config.base_url == "https://env.example/v1"
    assert factory.config.model == "env-model"
    assert factory.config.thinking == "enabled"
    assert factory.config.protocol == "openai_chat"


def test_environment_bootstrap_preserves_omitted_thinking(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    monkeypatch.delenv("OPENAI_THINKING", raising=False)

    factory = effective_gateway_factory(
        store=ModelConfigStore(tmp_path / "missing.json")
    )

    assert factory.config.thinking is None


def test_damaged_local_config_falls_back_to_environment(tmp_path, monkeypatch):
    path = tmp_path / "model_config.json"
    path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")

    store = ModelConfigStore(path)
    effective = store.load_effective()
    factory = effective_gateway_factory(store=store)

    assert effective.api_key == "environment-secret"
    assert effective.model == "environment-model"
    assert factory.config.api_key == "environment-secret"


def test_stored_config_preserves_unspecified_thinking():
    config = StoredModelConfig.from_dict(_payload(thinking=None))

    assert config.thinking is None
    assert config.gateway_config().thinking is None


def test_legacy_config_without_protocol_defaults_to_openai_chat():
    config = StoredModelConfig.from_dict({
        "api_key": "local-secret",
        "base_url": "https://example.test/v1",
        "model": "story-model",
        "thinking": True,
    })

    assert config.protocol == "openai_chat"
    assert config.gateway_config().protocol == "openai_chat"


def test_stored_false_thinking_maps_to_no_thinking_signal():
    config = StoredModelConfig.from_dict(_payload(thinking=False))

    assert config.gateway_config().thinking is None


def test_non_chat_protocol_rejects_enabled_thinking():
    with pytest.raises(ValueError, match="thinking is not supported"):
        StoredModelConfig.from_dict(_payload(protocol="gemini", thinking=True))

    with pytest.raises(ValueError, match="thinking is not supported"):
        StoredModelConfig.from_dict(
            _payload(protocol="anthropic_messages", thinking=True)
        )


def test_unknown_protocol_is_rejected():
    with pytest.raises(ValueError, match="protocol must be one of"):
        StoredModelConfig.from_dict(_payload(protocol="deepseek_native"))


def test_legacy_config_without_selection_migrates_to_custom():
    payload = {
        "api_key": "local-secret",
        "base_url": "https://example.test/v1",
        "model": "story-model",
        "thinking": False,
    }
    config = StoredModelConfig.from_dict(payload)

    assert config.preset_id == "custom"
    assert config.model_id == ""
    assert config.gateway_config().protocol == "openai_chat"


def test_unknown_preset_id_is_accepted_and_keeps_working():
    config = StoredModelConfig.from_dict(
        _payload(preset_id="retired-provider", model_id="old-model")
    )

    assert config.preset_id == "retired-provider"
    assert config.model_id == "old-model"
    assert config.gateway_config().model == "story-model"
    assert config.gateway_config().protocol == "openai_chat"


def test_preset_id_must_be_a_string():
    with pytest.raises(ValueError, match="preset_id must be a string"):
        StoredModelConfig.from_dict(_payload(preset_id=42))


def test_merge_payload_retains_selection_when_omitted(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save(StoredModelConfig.from_dict(
        _payload(preset_id="moonshot", model_id="kimi-k3")
    ))

    merged = store.merge_payload({"model": "other-model"})

    assert merged.preset_id == "moonshot"
    assert merged.model_id == "kimi-k3"


def test_merge_payload_overrides_selection_when_supplied(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save(StoredModelConfig.from_dict(
        _payload(preset_id="moonshot", model_id="kimi-k3")
    ))

    merged = store.merge_payload(
        {"preset_id": "custom", "model_id": None}
    )

    assert merged.preset_id == "custom"
    assert merged.model_id == "kimi-k3"


def test_empty_base_url_is_preserved_for_sdk_default():
    openai_config = StoredModelConfig.from_dict(_payload(base_url=""))
    gemini_config = StoredModelConfig.from_dict(
        _payload(base_url="", protocol="gemini", thinking=None)
    )

    assert openai_config.gateway_config().base_url == ""
    assert gemini_config.gateway_config().base_url == ""


def test_merge_payload_can_clear_base_url_to_official_default(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save(StoredModelConfig.from_dict(_payload()))

    merged = store.merge_payload({"base_url": ""})

    assert merged.base_url == ""
    assert merged.gateway_config().base_url == ""


def test_merge_payload_omitted_base_url_retains_current(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save(StoredModelConfig.from_dict(_payload()))

    merged = store.merge_payload({"model": "other-model"})

    assert merged.base_url == "https://example.test/v1"


def test_environment_without_base_url_uses_protocol_default(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_PROTOCOL", raising=False)

    factory = effective_gateway_factory(
        store=ModelConfigStore(tmp_path / "missing.json")
    )

    assert factory.config.protocol == "openai_chat"
    assert factory.config.base_url == ""


def test_environment_protocol_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    monkeypatch.setenv("OPENAI_PROTOCOL", "gemini")

    factory = effective_gateway_factory(
        store=ModelConfigStore(tmp_path / "missing.json")
    )

    assert factory.config.protocol == "gemini"


def test_blank_key_retains_configured_secret_and_explicit_clear_removes_it(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save(StoredModelConfig.from_dict(_payload()))

    retained = store.merge_payload(_payload(api_key="", model="new-model"))
    cleared = store.merge_payload(
        _payload(api_key="", clear_api_key=True)
    )

    assert retained.api_key == "local-secret"
    assert retained.model == "new-model"
    assert cleared.api_key == ""


def test_null_or_blank_text_payload_values_retain_current_values(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save(StoredModelConfig.from_dict(_payload()))

    merged = store.merge_payload({
        "api_key": None,
        "base_url": None,
        "model": "  ",
        "thinking": None,
    })

    assert merged.api_key == "local-secret"
    assert merged.base_url == "https://example.test/v1"
    assert merged.model == "story-model"
    assert merged.thinking is None


class _SuccessfulProvider(LLMProvider):
    def __init__(self, captured, config):
        self.captured = captured
        self.captured["config"] = config

    def generate(self, prompt, *, model, temperature):
        self.captured.update({
            "prompt": prompt,
            "model": model,
            "temperature": temperature,
        })
        return ProviderResult(content="connection ok")


def test_connection_check_uses_unsaved_candidate_and_reports_latency(tmp_path):
    captured = {}
    result = check_model_connection(
        _payload(),
        store=ModelConfigStore(tmp_path / "missing.json"),
        provider_factory=lambda config: _SuccessfulProvider(captured, config),
    )

    assert result["success"] is True
    assert result["model"] == "story-model"
    assert result["thinking"] is True
    assert result["protocol"] == "openai_chat"
    assert result["preset_id"] == "custom"
    assert result["model_id"] == ""
    assert result["latency_ms"] >= 0
    assert captured["config"].api_key == "local-secret"
    assert captured["model"] == "story-model"


def test_connection_error_is_classified_without_leaking_key(tmp_path):
    class FailingProvider(LLMProvider):
        def generate(self, *_args, **_kwargs):
            raise RuntimeError("provider rejected local-secret")

    result = check_model_connection(
        _payload(),
        store=ModelConfigStore(tmp_path / "missing.json"),
        provider_factory=lambda _config: FailingProvider(),
    )

    assert result["success"] is False
    assert result["error"]["code"] == "provider_error"
    assert "local-secret" not in json.dumps(result)
    assert "[REDACTED]" in result["error"]["message"]


def test_connection_error_classifies_forbidden_response(tmp_path):
    class ForbiddenProvider(LLMProvider):
        def generate(self, *_args, **_kwargs):
            raise RuntimeError("403 Forbidden")

    result = check_model_connection(
        _payload(),
        store=ModelConfigStore(tmp_path / "missing.json"),
        provider_factory=lambda _config: ForbiddenProvider(),
    )

    assert result["success"] is False
    assert result["error"]["code"] == "permission_denied"


def test_save_persists_before_hot_swapping_runtime(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")

    class Runtime:
        def __init__(self):
            self.factory = None
            self.persisted_when_swapped = False

        def replace_gateway_factory(self, factory, *, persist):
            persist()
            self.persisted_when_swapped = store.path.exists()
            self.factory = factory

    runtime = Runtime()
    status = save_model_config(_payload(), store=store, runtime=runtime)

    assert runtime.persisted_when_swapped is True
    assert isinstance(runtime.factory, SharedGatewayFactory)
    assert runtime.factory.config.model == "story-model"
    assert status["configured"] is True
    assert "api_key" not in status


def test_runtime_hot_swap_rebuilds_services_for_future_work(tmp_path):
    original = SharedGatewayFactory(
        config=StoredModelConfig.from_dict(_payload(model="old-model")).gateway_config()
    )
    replacement = SharedGatewayFactory(
        config=StoredModelConfig.from_dict(_payload(model="new-model")).gateway_config()
    )
    runtime = CampaignRuntime(
        "model-config-test",
        paths=CampaignPaths.from_data_dir(tmp_path / "campaign"),
        gateway_factory=original,
    )
    try:
        previous_services = runtime.services

        runtime.replace_gateway_factory(replacement)

        assert runtime.gateway_factory is replacement
        assert runtime.services is not previous_services
        assert runtime.services.gateway_factory is replacement
    finally:
        runtime.close(wait_for_jobs=True)


def test_concurrent_saves_merge_against_latest_persisted_config(tmp_path):
    class ObservedStore(ModelConfigStore):
        def __init__(self, path):
            super().__init__(path)
            self.merge_count = 0
            self.merge_lock = threading.Lock()
            self.second_merge = threading.Event()

        def merge_payload(self, payload):
            with self.merge_lock:
                self.merge_count += 1
                if self.merge_count == 2:
                    self.second_merge.set()
            return super().merge_payload(payload)

    store = ObservedStore(tmp_path / "model_config.json")
    store.save(StoredModelConfig.from_dict(_payload(api_key="old-secret")))
    first_inside_runtime = threading.Event()
    release_first = threading.Event()

    class SerializedRuntime:
        def __init__(self):
            self.lock = threading.Lock()
            self.calls = 0

        def replace_gateway_factory(self, _factory, *, persist):
            with self.lock:
                self.calls += 1
                if self.calls == 1:
                    first_inside_runtime.set()
                    assert release_first.wait(timeout=2)
                persist()

    runtime = SerializedRuntime()
    errors = []

    def save(payload):
        try:
            save_model_config(payload, store=store, runtime=runtime)
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(
        target=save,
        args=(_payload(api_key="new-secret", model="first-model"),),
    )
    second = threading.Thread(
        target=save,
        args=(_payload(api_key="", model="second-model"),),
    )
    first.start()
    assert first_inside_runtime.wait(timeout=2)
    second.start()
    store.second_merge.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert errors == []
    assert not first.is_alive()
    assert not second.is_alive()
    assert store.load_local().api_key == "new-secret"
    assert store.load_local().model == "second-model"


def test_save_rejects_missing_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = ModelConfigStore(tmp_path / "missing.json")

    with pytest.raises(ValueError, match="API Key is required"):
        save_model_config(
            _payload(api_key="", clear_api_key=True),
            store=store,
            runtime=object(),
        )
