"""models.dev catalog adapter (Issue 02).

Read-only directory layer: fetch ``https://models.dev/api.json``, normalize
provider/model rows onto the Issue 01 protocols, and fall back through
last-good cache to the shipped trimmed snapshot. Never touches user model
configuration and never loads upstream npm packages (``npm`` is only a
string key for the explicit protocol map below).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import APP_CONFIG_DIR

MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_TIMEOUT_S = 10.0
MODELS_DEV_MAX_BYTES = 20 * 1024 * 1024

#: Explicit upstream npm -> Issue 01 protocol mapping. Anything not listed
#: here is skipped, never guessed.
NPM_PROTOCOL_MAP: dict[str, str] = {
    "@ai-sdk/openai": "openai_responses",
    "@ai-sdk/openai-compatible": "openai_chat",
    "@ai-sdk/anthropic": "anthropic_messages",
    "@ai-sdk/google": "gemini",
}

SHAPE_PROTOCOL_MAP: dict[str, str] = {
    "responses": "openai_responses",
    "completions": "openai_chat",
}

#: Native npm packages that may omit ``api`` only for their own official
#: provider id (empty base_url = SDK official default). Any other provider
#: with a native npm but no endpoint is skipped instead of being pointed
#: at someone else's official address.
NATIVE_OFFICIAL_IDS: dict[str, str] = {
    "@ai-sdk/openai": "openai",
    "@ai-sdk/anthropic": "anthropic",
    "@ai-sdk/google": "google",
}

SNAPSHOT_PATH = Path(__file__).with_name("models_dev_snapshot.json")
CACHE_PATH = APP_CONFIG_DIR / "models_dev_cache.json"
_CACHE_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    model_name: str
    protocol: str
    base_url: str
    status: str = ""


@dataclass(frozen=True)
class CatalogProvider:
    provider_id: str
    provider_name: str
    models: tuple[CatalogModel, ...]


@dataclass(frozen=True)
class SkippedProvider:
    provider_id: str
    reason: str


def normalize(api_payload: object) -> tuple[list[CatalogProvider], list[SkippedProvider]]:
    """Normalize a models.dev api.json-shaped payload.

    Raises ValueError on incompatible top-level schema so callers fall
    back to cache/snapshot instead of serving partial data.
    """
    if not isinstance(api_payload, dict):
        raise ValueError("models.dev payload must be a JSON object")
    providers: list[CatalogProvider] = []
    skipped: list[SkippedProvider] = []
    for provider_id, raw in api_payload.items():
        if provider_id.startswith("_"):
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"provider {provider_id!r} must be a JSON object")
        models_raw = raw.get("models")
        if not isinstance(models_raw, dict):
            raise ValueError(f"provider {provider_id!r} is missing 'models'")
        rows: list[CatalogModel] = []
        for model_id, model_raw in models_raw.items():
            if not isinstance(model_raw, dict):
                raise ValueError(
                    f"model {model_id!r} of {provider_id!r} must be a JSON object"
                )
            row = _resolve_row(provider_id, raw, model_id, model_raw)
            if row is None:
                skipped.append(SkippedProvider(
                    provider_id=f"{provider_id}/{model_id}",
                    reason=_row_skip_reason(provider_id, raw, model_raw),
                ))
                continue
            rows.append(row)
        if not rows:
            continue
        rows.sort(key=lambda row: row.model_id)
        providers.append(CatalogProvider(
            provider_id=provider_id,
            provider_name=str(raw.get("name") or provider_id),
            models=tuple(rows),
        ))
    providers.sort(key=lambda provider: provider.provider_id)
    return providers, skipped


def _model_provider_block(model_raw: dict[str, Any]) -> dict[str, Any]:
    """Return the per-model ``provider`` override block (real upstream key).

    Upstream carries overrides as ``model.provider: {npm?, api?, shape?}``.
    """
    block = model_raw.get("provider")
    return block if isinstance(block, dict) else {}


def _resolve_row(
    provider_id: str,
    provider_raw: dict[str, Any],
    model_id: str,
    model_raw: dict[str, Any],
) -> CatalogModel | None:
    """Resolve one model row; None when the row must be skipped."""
    block = _model_provider_block(model_raw)
    npm = block.get("npm", provider_raw.get("npm"))
    if npm not in NPM_PROTOCOL_MAP:
        return None
    protocol = NPM_PROTOCOL_MAP[npm]
    shape = block.get("shape")
    if shape is not None:
        if protocol not in {"openai_responses", "openai_chat"}:
            return None
        protocol = SHAPE_PROTOCOL_MAP.get(shape)
        if protocol is None:
            return None
    api_raw = block.get("api", provider_raw.get("api"))
    base_url = str(api_raw or "").strip()
    if "${" in base_url:
        return None
    if not base_url and NATIVE_OFFICIAL_IDS.get(npm) != provider_id:
        return None
    return CatalogModel(
        model_id=str(model_raw.get("id") or model_id),
        model_name=str(model_raw.get("name") or model_id),
        protocol=protocol,
        base_url=base_url,
        status=str(model_raw.get("status") or ""),
    )


def _row_skip_reason(
    provider_id: str,
    provider_raw: dict[str, Any],
    model_raw: dict[str, Any],
) -> str:
    block = _model_provider_block(model_raw)
    npm = block.get("npm", provider_raw.get("npm"))
    if npm not in NPM_PROTOCOL_MAP:
        return f"auth_not_implemented_or_unknown_npm:{npm}"
    protocol = NPM_PROTOCOL_MAP[npm]
    shape = block.get("shape")
    if shape is not None and (
        shape not in SHAPE_PROTOCOL_MAP
        or protocol not in {"openai_responses", "openai_chat"}
    ):
        return f"unknown_or_incompatible_shape:{shape}"
    api_raw = block.get("api", provider_raw.get("api"))
    base_url = str(api_raw or "").strip()
    if "${" in base_url:
        return "unresolved_endpoint_template"
    if not base_url:
        return "missing_endpoint"
    return "unmappable"


def fetch(timeout: float = MODELS_DEV_TIMEOUT_S) -> dict[str, Any]:
    """Fetch live api.json. Raises on any failure (callers fall back)."""
    request = urllib.request.Request(
        MODELS_DEV_URL,
        headers={"Accept": "application/json", "User-Agent": "LoreLoop/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status != 200:
            raise ValueError(f"models.dev responded with HTTP {status}")
        raw = response.read(MODELS_DEV_MAX_BYTES + 1)
    if len(raw) > MODELS_DEV_MAX_BYTES:
        raise ValueError("models.dev response exceeds size guard")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"models.dev response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("models.dev payload must be a JSON object")
    return payload


def load_snapshot() -> dict[str, Any]:
    try:
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"models.dev snapshot is unavailable: {exc}") from exc


def load_cache(
    path: str | Path = CACHE_PATH,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_cache(
    payload: dict[str, Any],
    path: str | Path = CACHE_PATH,
) -> None:
    """Atomically persist a successfully refreshed payload. Never partial."""
    target = Path(path)
    with _CACHE_WRITE_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def _usable_providers(payload: object) -> list[CatalogProvider]:
    providers, _ = normalize(payload)
    if not providers:
        raise ValueError("models.dev catalog has no supported providers")
    return providers


def read_catalog(
    *,
    refresh: bool = False,
    timeout: float = MODELS_DEV_TIMEOUT_S,
    cache_path: str | Path = CACHE_PATH,
    fetch_fn=None,
) -> dict[str, Any]:
    """Read locally; explicit refresh tries live before cache and snapshot.

    Refresh failures never clobber a good cache and never touch user
    model configuration. Returns a JSON-serializable response payload
    with a ``source`` marker.
    """
    if refresh:
        try:
            payload = fetch_fn() if fetch_fn is not None else fetch(timeout=timeout)
            providers = _usable_providers(payload)
            save_cache(payload, cache_path)
            return {"source": "live", "providers": _public_providers(providers)}
        except Exception:
            pass
    try:
        cached = load_cache(cache_path)
        if cached is not None:
            providers = _usable_providers(cached)
            return {"source": "cache", "providers": _public_providers(providers)}
    except Exception:
        pass
    try:
        providers = _usable_providers(load_snapshot())
    except Exception:
        return {"source": "none", "providers": []}
    return {"source": "snapshot", "providers": _public_providers(providers)}


def _public_providers(providers: list[CatalogProvider]) -> list[dict[str, Any]]:
    return [
        {
            "provider_id": provider.provider_id,
            "provider_name": provider.provider_name,
            "models": [
                {
                    "model_id": model.model_id,
                    "model_name": model.model_name,
                    "protocol": model.protocol,
                    "base_url": model.base_url,
                    "status": model.status,
                }
                for model in provider.models
            ],
        }
        for provider in providers
    ]


def catalog_response(
    *,
    refresh: bool = False,
    timeout: float = MODELS_DEV_TIMEOUT_S,
    cache_path: str | Path = CACHE_PATH,
    fetch_fn=None,
) -> dict[str, Any]:
    started = time.perf_counter()
    payload = read_catalog(
        timeout=timeout, cache_path=cache_path, fetch_fn=fetch_fn, refresh=refresh,
    )
    payload["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return payload


__all__ = [
    "CACHE_PATH",
    "MODELS_DEV_MAX_BYTES",
    "MODELS_DEV_TIMEOUT_S",
    "MODELS_DEV_URL",
    "NATIVE_OFFICIAL_IDS",
    "NPM_PROTOCOL_MAP",
    "SHAPE_PROTOCOL_MAP",
    "SNAPSHOT_PATH",
    "CatalogModel",
    "CatalogProvider",
    "SkippedProvider",
    "catalog_response",
    "fetch",
    "load_cache",
    "load_snapshot",
    "normalize",
    "read_catalog",
    "save_cache",
]
