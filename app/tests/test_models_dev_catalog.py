import json

import pytest

from app.runtime.models_dev_catalog import (
    catalog_response,
    load_snapshot,
    normalize,
    read_catalog,
    save_cache,
)


def test_local_read_never_fetches_and_uses_cache_or_snapshot(tmp_path):
    def forbidden_fetch():
        pytest.fail("Opening settings must not contact the upstream catalog")

    cache = tmp_path / "catalog.json"
    snapshot = catalog_response( cache_path=cache, fetch_fn=forbidden_fetch)
    assert snapshot["source"] == "snapshot"
    assert snapshot["providers"]
    save_cache(_payload(), cache)
    cached = catalog_response( cache_path=cache, fetch_fn=forbidden_fetch)
    assert cached["source"] == "cache"
    assert cached["providers"]


def _payload(extra=None, **overrides):
    base = {
        "openai": {
            "id": "openai",
            "name": "OpenAI",
            "npm": "@ai-sdk/openai",
            "models": {
                "gpt-4o": {"id": "gpt-4o", "name": "GPT-4o"},
            },
        },
        "moonshot": {
            "id": "moonshot",
            "name": "Moonshot",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://api.moonshot.ai/v1",
            "models": {
                "kimi-k3": {
                    "id": "kimi-k3",
                    "name": "Kimi K3",
                    "status": "beta",
                },
            },
        },
        "bedrock": {
            "id": "bedrock",
            "name": "Bedrock",
            "npm": "@ai-sdk/amazon-bedrock",
            "models": {
                "x": {"id": "x", "name": "X"},
            },
        },
        "mystery": {
            "id": "mystery",
            "name": "Mystery",
            "npm": "@ai-sdk/openai-compatible",
            "models": {
                "m": {"id": "m", "name": "M"},
            },
        },
        "proxy-openai": {
            "id": "proxy-openai",
            "name": "Proxy",
            "npm": "@ai-sdk/openai",
            "models": {
                "m": {"id": "m", "name": "M"},
            },
        },
    }
    base.update(extra or {})
    base.update(overrides)
    return base


def _rows(providers):
    return {
        (provider.provider_id, model.model_id): model
        for provider in providers
        for model in provider.models
    }


def test_four_npm_values_map_to_four_protocols():
    providers, skipped = normalize(_payload({
        "anthropic": {
            "id": "anthropic",
            "name": "Anthropic",
            "npm": "@ai-sdk/anthropic",
            "models": {"c": {"id": "c", "name": "C"}},
        },
        "google": {
            "id": "google",
            "name": "Google",
            "npm": "@ai-sdk/google",
            "models": {"g": {"id": "g", "name": "G"}},
        },
    }))

    rows = _rows(providers)
    assert rows[("openai", "gpt-4o")].protocol == "openai_responses"
    assert rows[("openai", "gpt-4o")].base_url == ""
    assert rows[("moonshot", "kimi-k3")].protocol == "openai_chat"
    assert rows[("moonshot", "kimi-k3")].base_url == "https://api.moonshot.ai/v1"
    assert rows[("moonshot", "kimi-k3")].status == "beta"
    assert rows[("anthropic", "c")].protocol == "anthropic_messages"
    assert rows[("google", "g")].protocol == "gemini"
    assert {s.provider_id for s in skipped} == {
        "bedrock/x", "mystery/m", "proxy-openai/m",
    }


def test_skip_reasons_are_explicit():
    _, skipped = normalize(_payload())
    reasons = {s.provider_id: s.reason for s in skipped}

    assert reasons["bedrock/x"].startswith("auth_not_implemented_or_unknown_npm")
    assert reasons["mystery/m"] == "missing_endpoint"
    assert reasons["proxy-openai/m"] == "missing_endpoint"


def test_native_npm_without_official_id_is_skipped():
    providers, skipped = normalize(_payload({
        "impostor": {
            "id": "impostor",
            "name": "Impostor",
            "npm": "@ai-sdk/anthropic",
            "models": {"m": {"id": "m", "name": "M"}},
        },
    }))

    assert all(p.provider_id != "impostor" for p in providers)
    assert {s.provider_id for s in skipped} >= {"impostor/m", "proxy-openai/m"}


def _go_payload():
    # Real api.json shape (trimmed): per-model overrides live under
    # model.provider, not as top-level model npm/api keys.
    return {
        "opencode-go": {
            "id": "opencode-go",
            "name": "OpenCode Go",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://opencode.ai/zen/go/v1",
            "models": {
                "grok-4.6": {
                    "id": "grok-4.6",
                    "name": "Grok 4.6",
                    "provider": {"npm": "@ai-sdk/openai"},
                },
                "minimax-m3": {
                    "id": "minimax-m3",
                    "name": "MiniMax M3",
                    "provider": {"npm": "@ai-sdk/anthropic"},
                },
                "kimi-k3": {"id": "kimi-k3", "name": "Kimi K3"},
            },
        },
    }


def test_model_provider_block_overrides_protocol():
    providers, skipped = normalize(_go_payload())

    assert skipped == []
    rows = _rows(providers)
    assert rows[("opencode-go", "grok-4.6")].protocol == "openai_responses"
    assert rows[("opencode-go", "grok-4.6")].base_url == (
        "https://opencode.ai/zen/go/v1"
    )
    assert rows[("opencode-go", "minimax-m3")].protocol == "anthropic_messages"
    assert rows[("opencode-go", "kimi-k3")].protocol == "openai_chat"


def test_shape_override_maps_openai_wire_protocols():
    providers, skipped = normalize(_payload({
        "shape-provider": {
            "id": "shape-provider",
            "name": "Shape Provider",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://shape.example/v1",
            "models": {
                "responses": {
                    "id": "responses",
                    "name": "Responses",
                    "provider": {"shape": "responses"},
                },
                "completions": {
                    "id": "completions",
                    "name": "Completions",
                    "provider": {"shape": "completions"},
                },
                "unknown": {
                    "id": "unknown",
                    "name": "Unknown",
                    "provider": {"shape": "future-shape"},
                },
            },
        },
    }))

    rows = _rows(providers)
    assert rows[("shape-provider", "responses")].protocol == "openai_responses"
    assert rows[("shape-provider", "completions")].protocol == "openai_chat"
    reasons = {s.provider_id: s.reason for s in skipped}
    assert reasons["shape-provider/unknown"] == (
        "unknown_or_incompatible_shape:future-shape"
    )


def test_template_endpoints_are_filtered_per_row():
    providers, skipped = normalize(_payload({
        "cloud": {
            "id": "cloud",
            "name": "Cloud",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://api.cloud.example/${ACCOUNT_ID}/v1",
            "models": {
                "bad": {"id": "bad", "name": "Bad"},
                "good": {
                    "id": "good",
                    "name": "Good",
                    "provider": {
                        "npm": "@ai-sdk/openai-compatible",
                        "api": "https://fixed.example/v1",
                    },
                },
            },
        },
        "vertex": {
            "id": "vertex",
            "name": "Vertex",
            "npm": "@ai-sdk/unknown-vertex",
            "models": {
                "maas": {
                    "id": "maas",
                    "name": "Maas",
                    "provider": {
                        "npm": "@ai-sdk/openai-compatible",
                        "api": "https://${ENDPOINT}/v1",
                    },
                },
            },
        },
    }))

    rows = _rows(providers)
    assert ("cloud", "bad") not in rows
    assert ("vertex", "maas") not in rows
    assert rows[("cloud", "good")].base_url == "https://fixed.example/v1"
    reasons = {s.provider_id: s.reason for s in skipped}
    assert reasons["cloud/bad"] == "unresolved_endpoint_template"
    assert reasons["vertex/maas"] == "unresolved_endpoint_template"


def test_incompatible_schema_raises_for_fallback():
    with pytest.raises(ValueError):
        normalize(["not", "a", "dict"])
    with pytest.raises(ValueError):
        normalize({"p": {"id": "p", "npm": "@ai-sdk/openai"}})


def test_live_success_updates_cache(tmp_path):
    cache = tmp_path / "cache.json"
    result = read_catalog(
        refresh=True,
        cache_path=cache,
        fetch_fn=lambda: _payload(),
    )

    assert result["source"] == "live"
    assert cache.exists()
    assert json.loads(cache.read_text(encoding="utf-8")) == _payload()


def test_refresh_failure_keeps_good_cache(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(_payload()), encoding="utf-8")
    before = cache.read_bytes()

    def _boom():
        raise TimeoutError("upstream down")

    result = read_catalog(refresh=True, cache_path=cache, fetch_fn=_boom)

    assert result["source"] == "cache"
    assert cache.read_bytes() == before
    ids = [p["provider_id"] for p in result["providers"]]
    assert "openai" in ids and "moonshot" in ids


def test_invalid_json_falls_back_without_clobbering(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(_payload()), encoding="utf-8")

    result = read_catalog(
        refresh=True,
        cache_path=cache,
        fetch_fn=lambda: "this is not json-shaped",
    )

    assert result["source"] == "cache"
    assert json.loads(cache.read_text(encoding="utf-8")) == _payload()


def test_schema_drift_to_empty_catalog_keeps_good_cache(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps(_payload()), encoding="utf-8")
    before = cache.read_bytes()
    drifted = {
        "openai": {
            "id": "openai",
            "name": "OpenAI",
            "sdk": "@ai-sdk/openai",
            "models": {"gpt-4o": {"id": "gpt-4o", "name": "GPT-4o"}},
        },
    }

    result = read_catalog(refresh=True, cache_path=cache, fetch_fn=lambda: drifted)

    assert result["source"] == "cache"
    assert cache.read_bytes() == before
    assert result["providers"]


def test_no_cache_uses_shipped_snapshot(tmp_path):
    result = read_catalog(
        refresh=True,
        cache_path=tmp_path / "missing.json",
        fetch_fn=lambda: (_ for _ in ()).throw(TimeoutError("down")),
    )

    assert result["source"] == "snapshot"
    assert len(result["providers"]) > 100
    openai = next(
        p for p in result["providers"] if p["provider_id"] == "openai"
    )
    assert openai["models"]
    assert not (tmp_path / "missing.json").exists()


def test_corrupt_cache_falls_through_to_snapshot(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text("{broken", encoding="utf-8")

    def _boom():
        raise ConnectionError("down")

    result = read_catalog(refresh=True, cache_path=cache, fetch_fn=_boom)

    assert result["source"] == "snapshot"


def test_missing_snapshot_returns_empty_catalog(tmp_path, monkeypatch):
    import app.runtime.models_dev_catalog as catalog

    monkeypatch.setattr(
        catalog, "SNAPSHOT_PATH", tmp_path / "no-snapshot.json"
    )

    def _boom():
        raise ConnectionError("down")

    result = read_catalog(
        refresh=True,
        cache_path=tmp_path / "missing.json", fetch_fn=_boom
    )

    assert result == {"source": "none", "providers": []}


def test_api_contract_has_no_secrets(tmp_path):
    result = catalog_response(
        refresh=True,
        cache_path=tmp_path / "missing.json",
        fetch_fn=lambda: _payload(),
    )

    assert result["source"] == "live"
    assert result["latency_ms"] >= 0
    blob = json.dumps(result)
    assert "api_key" not in blob
    assert "model_config.json" not in blob
    assert set(result["providers"][0].keys()) == {
        "provider_id", "provider_name", "models",
    }
    assert set(result["providers"][0]["models"][0].keys()) == {
        "model_id", "model_name", "protocol", "base_url", "status",
    }


def test_refresh_never_touches_user_model_config(tmp_path):
    config_path = tmp_path / "model_config.json"
    config_path.write_text('{"sentinel": true}', encoding="utf-8")
    before = config_path.read_bytes()

    read_catalog(
        refresh=True,
        cache_path=tmp_path / "cache.json",
        fetch_fn=lambda: _payload(),
    )

    assert config_path.read_bytes() == before


def test_shipped_snapshot_parses_and_is_nonempty():
    snapshot = load_snapshot()
    providers, _ = normalize(snapshot)

    assert len(providers) > 100
    assert sum(len(p.models) for p in providers) > 1000
    for provider_id, provider in snapshot.items():
        if provider_id.startswith("_"):
            continue
        assert "id" not in provider
        assert "doc" not in provider
        assert provider.get("name") != provider_id
        for model_id, model in provider["models"].items():
            assert "id" not in model
            assert model.get("name") != model_id
            assert model.get("status") != ""


def test_shipped_snapshot_go_rows_and_no_templates():
    providers, _ = normalize(load_snapshot())
    rows = _rows(providers)

    assert rows[("opencode-go", "grok-4.6")].protocol == "openai_responses"
    assert rows[("opencode-go", "minimax-m3")].protocol == "anthropic_messages"
    assert rows[("opencode-go", "kimi-k3")].protocol == "openai_chat"
    assert rows[("sakana", "fugu")].protocol == "openai_responses"
    for provider in providers:
        for model in provider.models:
            assert "${" not in model.base_url
            assert model.protocol in {
                "openai_responses",
                "openai_chat",
                "anthropic_messages",
                "gemini",
            }
