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
    ModelConfigSet,
    ModelConfigStore,
    StoredModelConfig,
    activate_model_config,
    delete_model_config,
    effective_gateway_factory,
    list_model_configs,
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


def _service_payload(**overrides):
    return {
        "api_key": "local-secret",
        "base_url": "https://example.test/v1",
        "model": "story-model",
        "thinking": True,
        "protocol": "openai_chat",
        "preset_id": "custom",
        "model_id": "",
        "name": "测试服务",
        **overrides,
    }


def _empty_set():
    return ModelConfigSet((), "")


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


class _Runtime:
    def __init__(self, store):
        self.store = store
        self.campaign_id = "test-campaign"
        self.factory = None
        self.persisted_when_swapped = False

    def replace_gateway_factory(self, factory, *, persist):
        persist()
        self.persisted_when_swapped = self.store.path.exists()
        self.factory = factory


def test_migrates_v2_plaintext_to_v3_custom_service(tmp_path):
    path = tmp_path / "model_config.json"
    path.write_text(json.dumps(_service_payload()), encoding="utf-8")

    store = ModelConfigStore(path)
    config_set = store.load_set()

    assert config_set.active_config_id
    assert len(config_set.services) == 1
    service = config_set.services[0]
    assert service.api_key == "local-secret"
    assert service.preset_id == "custom"
    assert service.name == "自定义服务"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["format_version"] == MODEL_CONFIG_FORMAT_VERSION
    assert "services" in persisted


def test_migrates_v2_non_custom_keeps_identity(tmp_path):
    path = tmp_path / "model_config.json"
    path.write_text(json.dumps(
        _service_payload(preset_id="moonshot", model_id="kimi-k3", name=""),
    ), encoding="utf-8")

    store = ModelConfigStore(path)
    config_set = store.load_set()

    service = config_set.services[0]
    assert service.preset_id == "moonshot"
    assert service.model_id == "kimi-k3"
    assert service.name == "moonshot"


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "model_config.json"
    path.write_text(json.dumps(_service_payload()), encoding="utf-8")
    store = ModelConfigStore(path)
    first = store.load_set()
    second = store.load_set()

    assert first == second
    assert json.loads(path.read_text(encoding="utf-8"))["format_version"] == (
        MODEL_CONFIG_FORMAT_VERSION
    )


def test_migration_failure_preserves_original_file(tmp_path):
    class BrokenProtector(_ReversibleProtector):
        def protect(self, _secret):
            raise RuntimeError("protection failed")

    path = tmp_path / "model_config.json"
    original = json.dumps(_service_payload(), ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    store = ModelConfigStore(path, secret_protector=BrokenProtector())

    with pytest.raises(RuntimeError, match="protection failed"):
        store.load_set()

    assert path.read_text(encoding="utf-8") == original


def test_v3_load_round_trips_protected_keys_per_service(tmp_path):
    path = tmp_path / "model_config.json"
    store = ModelConfigStore(path, secret_protector=_ReversibleProtector())
    store.save_set(ModelConfigSet((
        store.resolve_draft(_service_payload(name="A"), _empty_set()),
        store.resolve_draft(
            _service_payload(
                api_key="second-key",
                name="B",
                preset_id="moonshot",
                model_id="kimi-k3",
            ),
            _empty_set(),
        ),
    ), ""))

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["api_key_protection"] == "test-current-user"
    assert "local-secret" not in path.read_text(encoding="utf-8")
    assert "second-key" not in path.read_text(encoding="utf-8")
    for service in persisted["services"]:
        assert service["api_key_protected"].startswith("protected:")

    reloaded = store.load_set()
    keys = {s.name: s.api_key for s in reloaded.services}
    assert keys == {"A": "local-secret", "B": "second-key"}


def test_list_projection_has_no_keys_and_marks_active(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    first = store.resolve_draft(_service_payload(name="A"), _empty_set())
    second = store.resolve_draft(
        _service_payload(
            api_key="second-key",
            name="B",
            preset_id="moonshot",
            model_id="kimi-k3",
        ),
        _empty_set(),
    )
    store.save_set(ModelConfigSet((first, second), second.config_id))

    payload = list_model_configs(store=store)

    assert payload["active_config_id"] == second.config_id
    blob = json.dumps(payload)
    assert "local-secret" not in blob
    assert "second-key" not in blob
    by_name = {s["name"]: s for s in payload["services"]}
    assert by_name["B"]["has_api_key"] is True
    assert by_name["B"]["active"] is True
    assert by_name["example.test · story-model"]["active"] is False
    # Full edit projection is present so the edit form can repopulate.
    assert by_name["B"]["protocol"] == "openai_chat"
    assert by_name["B"]["base_url"] == "https://example.test/v1"
    assert by_name["B"]["thinking"] is True
    assert by_name["B"]["model_id"] == "kimi-k3"


def test_new_service_requires_its_own_key(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save_set(ModelConfigSet((
        store.resolve_draft(_service_payload(api_key="active-key"), _empty_set()),
    ), ""))
    config_set = store.load_set()
    config_set = config_set.set_active(config_set.services[0].config_id)

    with pytest.raises(ValueError, match="API Key"):
        store.resolve_draft(
            _service_payload(api_key="", preset_id="moonshot"),
            config_set,
        )


def test_edit_same_endpoint_reuses_key(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    service = store.resolve_draft(
        _service_payload(api_key="keep-key", name="A"), _empty_set()
    )
    store.save_set(ModelConfigSet((service,), service.config_id))
    config_set = store.load_set()

    edited = store.resolve_draft(
        _service_payload(
            config_id=service.config_id, api_key="", model="new-model", name="A",
        ),
        config_set,
    )

    assert edited.api_key == "keep-key"
    assert edited.model == "new-model"


def test_edit_changed_endpoint_requires_new_key(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    service = store.resolve_draft(
        _service_payload(api_key="keep-key", name="A"), _empty_set()
    )
    store.save_set(ModelConfigSet((service,), service.config_id))
    config_set = store.load_set()

    with pytest.raises(ValueError, match="请填写此服务的 API Key"):
        store.resolve_draft(
            _service_payload(
                config_id=service.config_id,
                api_key="",
                base_url="https://other.test/v1",
                name="A",
            ),
            config_set,
        )


def test_edit_cannot_change_provider_identity(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    service = store.resolve_draft(
        _service_payload(api_key="keep-key", name="A", preset_id="moonshot"),
        _empty_set(),
    )
    store.save_set(ModelConfigSet((service,), service.config_id))
    config_set = store.load_set()

    with pytest.raises(ValueError, match="Provider 身份"):
        store.resolve_draft(
            _service_payload(
                config_id=service.config_id,
                api_key="",
                preset_id="openai",
                name="A",
            ),
            config_set,
        )


def test_duplicate_non_custom_provider_rejected(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    service = store.resolve_draft(
        _service_payload(api_key="k1", name="A", preset_id="moonshot"),
        _empty_set(),
    )
    store.save_set(ModelConfigSet((service,), service.config_id))
    config_set = store.load_set()

    with pytest.raises(ValueError, match="已存在使用此 Provider 的服务"):
        store.resolve_draft(
            _service_payload(api_key="k2", preset_id="moonshot"),
            config_set,
        )


def test_multiple_custom_services_allowed(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    a = store.resolve_draft(_service_payload(api_key="k1", name="自定义一"), _empty_set())
    b = store.resolve_draft(_service_payload(api_key="k2", name="自定义二"), _empty_set())
    store.save_set(ModelConfigSet((a, b), a.config_id))

    config_set = store.load_set()
    assert len(config_set.services) == 2
    assert config_set.active_config_id == a.config_id


def test_concurrent_duplicate_add_does_not_overwrite(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    service = store.resolve_draft(
        _service_payload(api_key="k1", name="A", preset_id="moonshot"),
        _empty_set(),
    )
    store.save_set(ModelConfigSet((service,), service.config_id))

    errors = []
    def add(payload):
        try:
            save_model_config(payload, store=store, runtime=object())
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(
            target=add,
            args=(_service_payload(api_key="k2", preset_id="moonshot"),),
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(errors) == 4
    assert len(store.load_set().services) == 1


def test_activate_swaps_runtime_and_persists(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    a = store.resolve_draft(_service_payload(api_key="k1", name="A"), _empty_set())
    b = store.resolve_draft(
        _service_payload(api_key="k2", name="B", preset_id="moonshot"), _empty_set()
    )
    store.save_set(ModelConfigSet((a, b), a.config_id))
    runtime = _Runtime(store)

    activate_model_config(b.config_id, store=store, runtime=runtime)

    assert runtime.persisted_when_swapped is True
    assert isinstance(runtime.factory, SharedGatewayFactory)
    assert runtime.factory.config.model == "story-model"
    assert store.load_set().active_config_id == b.config_id


def test_delete_active_is_rejected(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    a = store.resolve_draft(_service_payload(api_key="k1", name="A"), _empty_set())
    store.save_set(ModelConfigSet((a,), a.config_id))

    with pytest.raises(ValueError, match="不能删除当前正在使用"):
        delete_model_config(a.config_id, store=store, runtime=object())


def test_delete_inactive_removes_it(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    a = store.resolve_draft(_service_payload(api_key="k1", name="A"), _empty_set())
    b = store.resolve_draft(
        _service_payload(api_key="k2", name="B", preset_id="moonshot"), _empty_set()
    )
    store.save_set(ModelConfigSet((a, b), a.config_id))

    delete_model_config(b.config_id, store=store, runtime=object())

    config_set = store.load_set()
    assert len(config_set.services) == 1
    assert config_set.active_config_id == a.config_id


def test_delete_unknown_config_id_is_rejected(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")

    with pytest.raises(ValueError, match="未知的服务配置"):
        delete_model_config("nope", store=store, runtime=object())


def test_activate_unknown_config_id_is_rejected(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")

    with pytest.raises(ValueError, match="未知的服务配置"):
        activate_model_config("nope", store=store, runtime=object())


def test_activate_without_key_is_rejected(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    service = StoredModelConfig(config_id="cid", name="A", api_key="")
    store.save_set(ModelConfigSet((service,), service.config_id))

    with pytest.raises(ValueError, match="缺少 API Key"):
        activate_model_config(service.config_id, store=store, runtime=object())


def test_saved_but_not_enabled_service_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    store = ModelConfigStore(tmp_path / "model_config.json")
    service = store.resolve_draft(_service_payload(api_key="k1", name="A"), _empty_set())
    store.save_set(ModelConfigSet((service,), ""))

    config_set = store.load_set()
    assert config_set.active_config_id == ""
    assert config_set.active() is None
    # No active service: effective config is the environment bootstrap,
    # never the first saved service.
    effective = store.load_effective()
    assert effective.api_key == "environment-secret"
    assert effective.model != service.model


def test_unknown_config_id_is_rejected_on_save(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")

    with pytest.raises(ValueError, match="未知的服务配置"):
        save_model_config(
            _service_payload(config_id="does-not-exist", name="A"),
            store=store,
            runtime=object(),
        )


def test_unknown_config_id_is_rejected_on_test(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")

    result = check_model_connection(
        _service_payload(config_id="does-not-exist", name="A"),
        store=store,
        provider_factory=lambda _config: _SuccessfulProvider({}, _config),
    )

    assert result["success"] is False
    assert result["error"]["code"] == "api_key_missing"
    assert "未知的服务配置" in result["error"]["message"]


def test_custom_services_need_no_name_and_display_safe_target(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    payload = _service_payload(preset_id="custom", base_url="https://user:password@example.test/v1?key=secret")
    payload.pop("name")
    save_model_config(payload, store=store)
    save_model_config(payload, store=store)
    rows = list_model_configs(store=store)["services"]
    assert len(rows) == 2
    assert rows[0]["config_id"] != rows[1]["config_id"]
    assert rows[0]["name"] == "example.test · story-model"


def test_save_new_persists_and_swaps_when_activate(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    runtime = _Runtime(store)

    payload = save_model_config(
        _service_payload(api_key="k1", name="A", activate=True),
        store=store,
        runtime=runtime,
    )

    assert runtime.persisted_when_swapped is True
    assert isinstance(runtime.factory, SharedGatewayFactory)
    assert store.load_set().services[0].api_key == "k1"
    assert payload["active_config_id"] == store.load_set().services[0].config_id


def test_save_new_without_activate_does_not_swap(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    runtime = _Runtime(store)

    save_model_config(
        _service_payload(api_key="k1", name="A", activate=False),
        store=store,
        runtime=runtime,
    )

    assert runtime.factory is None
    assert runtime.persisted_when_swapped is False


def test_edit_active_service_hot_swaps(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    a = store.resolve_draft(_service_payload(api_key="k1", name="A"), _empty_set())
    store.save_set(ModelConfigSet((a,), a.config_id))
    runtime = _Runtime(store)

    save_model_config(
        _service_payload(
            config_id=a.config_id, api_key="", model="new-model", name="A",
        ),
        store=store,
        runtime=runtime,
    )

    assert runtime.persisted_when_swapped is True
    assert runtime.factory.config.model == "new-model"


def test_save_rejects_missing_key_on_new(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")

    with pytest.raises(ValueError, match="需要填写 API Key"):
        save_model_config(
            _service_payload(api_key="", name="A"),
            store=store,
            runtime=object(),
        )


def test_connection_check_uses_unsaved_candidate_and_reports_latency(tmp_path):
    captured = {}
    result = check_model_connection(
        _service_payload(),
        store=ModelConfigStore(tmp_path / "missing.json"),
        provider_factory=lambda config: _SuccessfulProvider(captured, config),
    )

    assert result["success"] is True
    assert result["model"] == "story-model"
    assert result["protocol"] == "openai_chat"
    assert result["latency_ms"] >= 0
    assert captured["config"].api_key == "local-secret"
    assert captured["model"] == "story-model"


def test_connection_check_does_not_save(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    captured = {}

    check_model_connection(
        _service_payload(),
        store=store,
        provider_factory=lambda config: _SuccessfulProvider(captured, config),
    )

    assert not store.path.exists()


def test_connection_error_is_classified_without_leaking_key(tmp_path):
    class FailingProvider(LLMProvider):
        def generate(self, *_args, **_kwargs):
            raise RuntimeError("provider rejected local-secret")

    result = check_model_connection(
        _service_payload(),
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
        _service_payload(),
        store=ModelConfigStore(tmp_path / "missing.json"),
        provider_factory=lambda _config: ForbiddenProvider(),
    )

    assert result["success"] is False
    assert result["error"]["code"] == "permission_denied"


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


def test_only_opencode_go_selects_its_request_profile():
    opencode = StoredModelConfig(
        api_key="k", model="mimo-v2.5", preset_id="opencode-go"
    ).gateway_config()
    other = StoredModelConfig(
        api_key="k", model="story-model", preset_id="moonshot"
    ).gateway_config()

    assert opencode.request_profile == "opencode_go"
    assert other.request_profile is None
    other_provider = SharedGatewayFactory(config=other)._get_components()[0]
    assert "extra_headers" not in other_provider._model_settings(0.0)


def test_opencode_go_headers_follow_factory_session():
    factory = SharedGatewayFactory(
        config=StoredModelConfig(
            api_key="k", model="mimo-v2.5", preset_id="opencode-go"
        ).gateway_config(),
        session_id="save-a",
    )
    provider = factory._get_components()[0]

    assert provider._model_settings(0.0)["extra_headers"] == {
        "x-opencode-session": "save-a",
        "User-Agent": "LoreLoop/1.0",
    }
    factory.set_session_id("save-b")
    assert provider._model_settings(0.0)["extra_headers"][
        "x-opencode-session"
    ] == "save-b"


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

    assert effective.api_key == "environment-secret"
    assert effective.model == "environment-model"


def test_runtime_hot_swap_rebuilds_services_for_future_work(tmp_path):
    original = SharedGatewayFactory(
        config=StoredModelConfig(api_key="k", model="story-model").gateway_config()
    )
    replacement = SharedGatewayFactory(
        config=StoredModelConfig(
            api_key="k",
            model="new-model",
            preset_id="opencode-go",
        ).gateway_config()
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
        provider = replacement._get_components()[0]
        assert provider._model_settings(0.0)["extra_headers"][
            "x-opencode-session"
        ] == "model-config-test"
    finally:
        runtime.close(wait_for_jobs=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_windows_dpapi_current_user_round_trip():
    protector = DPAPICurrentUserProtector()

    protected = protector.protect("测试-api-key")

    assert protected
    assert "测试-api-key" not in protected
    assert protector.unprotect(protected) == "测试-api-key"


def test_plaintext_persist_uses_0600_permissions(tmp_path):
    store = ModelConfigStore(tmp_path / "model_config.json")
    store.save_set(ModelConfigSet((
        store.resolve_draft(_service_payload(name="A"), _empty_set()),
    ), ""))
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_v3_rejects_unknown_protocol_in_service(tmp_path):
    path = tmp_path / "model_config.json"
    path.write_text(json.dumps({
        "format_version": MODEL_CONFIG_FORMAT_VERSION,
        "active_config_id": "x",
        "services": [{
            "config_id": "x",
            "name": "A",
            "protocol": "deepseek_native",
            "model": "m",
        }],
    }), encoding="utf-8")
    store = ModelConfigStore(path)

    with pytest.raises(ValueError, match="protocol must be one of"):
        store.load_set()
