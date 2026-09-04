"""Regenerate the trimmed models.dev snapshot (maintainer use only).

Reads live https://models.dev/api.json, keeps only the fields the
catalog adapter consumes, and writes
app/runtime/models_dev_snapshot.json in api.json top-level shape
(plus underscore-prefixed meta keys the parser skips). Never touches
user config, cache, or tests (tests must not call this).
"""

import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO_ROOT / "app" / "runtime" / "models_dev_snapshot.json"

ATTRIBUTION = (
    "models.dev (https://github.com/anomalyco/models.dev), MIT License "
    "(see app/runtime/models_dev_LICENSE.txt). Trimmed snapshot; redundant "
    "defaults omitted; only display names, npm/api, non-empty status, and "
    "model provider npm/api/shape overrides retained."
)


def _trimmed_model(
    model_id: str, model_name: str, status: str, raw_model: dict,
) -> dict:
    """Keep display fields plus the per-model provider override block.

    ``shape`` is retained because models.dev uses it to distinguish OpenAI
    Responses from Chat Completions for individual models.
    """
    trimmed: dict[str, object] = {}
    if model_name != model_id:
        trimmed["name"] = model_name
    if status:
        trimmed["status"] = status
    block = raw_model.get("provider")
    if isinstance(block, dict):
        override = {}
        if block.get("npm") is not None:
            override["npm"] = block["npm"]
        if block.get("api") is not None:
            override["api"] = block["api"]
        if block.get("shape") is not None:
            override["shape"] = block["shape"]
        if override:
            trimmed["provider"] = override
    return trimmed


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from app.runtime.models_dev_catalog import fetch, normalize

    payload = fetch()
    providers, skipped = normalize(payload)
    today = datetime.date.today().isoformat()
    out: dict[str, object] = {
        "_attribution": f"{ATTRIBUTION} Generated {today}.",
        "_generated": today,
    }
    for provider in providers:
        raw = payload[provider.provider_id]
        raw_models = raw["models"]
        trimmed_provider: dict[str, object] = {}
        if provider.provider_name != provider.provider_id:
            trimmed_provider["name"] = provider.provider_name
        trimmed_provider["npm"] = raw.get("npm", "")
        if raw.get("api"):
            trimmed_provider["api"] = raw["api"]
        trimmed_provider["models"] = {
            model.model_id: _trimmed_model(
                model.model_id, model.model_name, model.status,
                raw_models.get(model.model_id) or {},
            )
            for model in provider.models
            if model.model_id in raw_models
        }
        out[provider.provider_id] = trimmed_provider
    SNAPSHOT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(
        f"snapshot: {len(providers)} providers, "
        f"{sum(len(p.models) for p in providers)} models, "
        f"{len(skipped)} skipped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
