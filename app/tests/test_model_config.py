import json
import stat
import threading

import pytest

from app.core.llm.config import SharedGatewayFactory
from app.core.llm.providers import ProviderResult
from app.runtime.model_config import (
    ModelConfigStore,
    StoredModelConfig,
    effective_gateway_factory,
    save_model_config,
    test_model_connection as check_model_connection,
)
from app.runtime.paths import CampaignPaths
from app.runtime.session import CampaignRuntime


def _payload(**overrides):
    return {
        "api_key": "local-secret",
        "base_url": "https://example.test/v1",
        "model": "story-model",
        "thinking": True,
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
    }
    assert "api_key" not in status
    assert "local-secret" not in json.dumps(status)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


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


class _SuccessfulProvider:
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
    assert result["latency_ms"] >= 0
    assert captured["config"].api_key == "local-secret"
    assert captured["model"] == "story-model"


def test_connection_error_is_classified_without_leaking_key(tmp_path):
    class FailingProvider:
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
    class ForbiddenProvider:
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
