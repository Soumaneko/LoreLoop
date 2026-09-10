from typing import Literal
from urllib.parse import urlsplit


RequestProfile = Literal["deepseek", "opencode_go"]
REQUEST_PROFILES: tuple[str, ...] = ("deepseek", "opencode_go")
OPENCODE_GO_SESSION_ID = "794wNhtK"


def request_profile_for_preset(preset_id: str) -> RequestProfile | None:
    if preset_id == "opencode-go":
        return "opencode_go"
    if preset_id == "deepseek":
        return "deepseek"
    return None


def request_profile_for_service(
    preset_id: str, base_url: str
) -> RequestProfile | None:
    profile = request_profile_for_preset(preset_id)
    if profile is not None:
        return profile
    if urlsplit(base_url).hostname == "api.deepseek.com":
        return "deepseek"
    return None


def request_headers(
    profile: RequestProfile | None, _session_id: str
) -> dict[str, str]:
    """Return headers required by a provider service, not by its wire protocol."""
    if profile is None:
        return {}
    if profile == "deepseek":
        return {}
    if profile == "opencode_go":
        return {
            "x-opencode-session": OPENCODE_GO_SESSION_ID,
            "User-Agent": "LoreLoop/1.0",
        }
    raise ValueError(f"Unsupported request profile: {profile}")


def map_thinking_request(
    profile: RequestProfile | None,
    model: str,
    thinking: bool | None,
) -> tuple[str | None, str | None]:
    """Map the UI toggle to service-specific request settings."""
    if thinking is True:
        return "enabled", None
    if thinking is False and profile == "deepseek":
        return "disabled", None
    if (
        thinking is False
        and profile == "opencode_go"
        and model.casefold().startswith("deepseek-")
    ):
        return None, "none"
    return None, None
