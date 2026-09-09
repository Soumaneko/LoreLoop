from typing import Literal


RequestProfile = Literal["opencode_go"]
REQUEST_PROFILES: tuple[str, ...] = ("opencode_go",)


def request_profile_for_preset(preset_id: str) -> RequestProfile | None:
    return "opencode_go" if preset_id == "opencode-go" else None


def request_headers(profile: RequestProfile | None, session_id: str) -> dict[str, str]:
    """Return headers required by a provider service, not by its wire protocol."""
    if profile is None:
        return {}
    if profile == "opencode_go":
        if not session_id:
            raise ValueError("OpenCode Go requires a stable session ID")
        return {
            "x-opencode-session": session_id,
            "User-Agent": "LoreLoop/1.0",
        }
    raise ValueError(f"Unsupported request profile: {profile}")
