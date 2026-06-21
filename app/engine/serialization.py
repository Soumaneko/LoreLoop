from dataclasses import asdict, is_dataclass
from typing import Any


def model_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return model_data(asdict(value))
    if isinstance(value, list):
        return [model_data(item) for item in value]
    if isinstance(value, dict):
        return {key: model_data(item) for key, item in value.items()}
    return value
