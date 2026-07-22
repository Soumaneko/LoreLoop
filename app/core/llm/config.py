import os
from dataclasses import dataclass
import threading
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class GatewayConfig:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    thinking: str | None = None

    def __post_init__(self) -> None:
        api_key = str(self.api_key).strip()
        base_url = str(self.base_url).strip()
        model = str(self.model).strip()
        thinking = self.thinking
        if thinking not in {None, "enabled", "disabled"}:
            raise ValueError("thinking must be 'enabled', 'disabled', or None")
        if not base_url:
            raise ValueError("base_url is required")
        if not model:
            raise ValueError("model is required")
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls, *, model: str | None = None) -> "GatewayConfig":
        thinking = os.environ.get("OPENAI_THINKING", "").strip().lower() or None
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get(
                "OPENAI_BASE_URL",
                "https://api.openai.com/v1",
            ),
            model=model or os.environ.get("OPENAI_MODEL", "gpt-4o"),
            thinking=thinking,
        )


class LLMConfig:
    @staticmethod
    def api_key() -> str:
        return GatewayConfig.from_env().api_key

    @staticmethod
    def base_url() -> str:
        return GatewayConfig.from_env().base_url

    @staticmethod
    def default_model() -> str:
        return GatewayConfig.from_env().model

    @staticmethod
    def thinking() -> str | None:
        return GatewayConfig.from_env().thinking

    @staticmethod
    def is_configured() -> bool:
        return GatewayConfig.from_env().is_configured


def create_gateway(
    model: Optional[str] = None,
    prompts_dir: Optional[str] = None,
    log_path: Optional[str] = None,
):
    from .gateway import LLMGateway
    from .prompts import PromptLoader
    from .providers import OpenAICompatibleProvider
    from .tracing import TraceLogger

    config = GatewayConfig.from_env(model=model)
    if not config.is_configured:
        raise RuntimeError(
            "OPENAI_API_KEY not set. Create a .env file (see .env.example) "
            "or set the environment variable."
        )

    tracer_kwargs = {}
    if log_path:
        tracer_kwargs["log_path"] = log_path

    return LLMGateway(
        provider=OpenAICompatibleProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            default_model=config.model,
            thinking=config.thinking,
        ),
        prompt_loader=PromptLoader(prompts_dir=prompts_dir),
        tracer=TraceLogger(**tracer_kwargs),
        default_model=config.model,
    )


class SharedGatewayFactory:
    """Create trace-isolated gateways backed by shared, long-lived clients."""

    def __init__(
        self,
        model: Optional[str] = None,
        prompts_dir: Optional[str] = None,
        log_path: Optional[str] = None,
        *,
        config: GatewayConfig | None = None,
    ) -> None:
        self.config = config or GatewayConfig.from_env(model=model)
        self.model = self.config.model
        self.prompts_dir = prompts_dir
        self.log_path = log_path
        self._components = None
        self._lock = threading.Lock()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def __call__(self):
        from .gateway import LLMGateway

        provider, prompt_loader, tracer, default_model = self._get_components()
        return LLMGateway(
            provider=provider,
            prompt_loader=prompt_loader,
            tracer=tracer,
            default_model=default_model,
        )

    def _get_components(self):
        if self._components is not None:
            return self._components
        if not self.config.is_configured:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Create a .env file (see .env.example) "
                "or set the environment variable."
            )

        from .prompts import PromptLoader
        from .providers import OpenAICompatibleProvider
        from .tracing import TraceLogger

        with self._lock:
            if self._components is None:
                tracer_kwargs = {}
                if self.log_path:
                    tracer_kwargs["log_path"] = self.log_path
                default_model = self.config.model
                self._components = (
                    OpenAICompatibleProvider(
                        api_key=self.config.api_key,
                        base_url=self.config.base_url,
                        default_model=default_model,
                        thinking=self.config.thinking,
                    ),
                    PromptLoader(prompts_dir=self.prompts_dir),
                    TraceLogger(**tracer_kwargs),
                    default_model,
                )
        return self._components
