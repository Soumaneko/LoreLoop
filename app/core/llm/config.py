import os
from functools import lru_cache
import threading
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@lru_cache(maxsize=1)
def _get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class LLMConfig:
    @staticmethod
    def api_key() -> str:
        return _get_env("OPENAI_API_KEY")

    @staticmethod
    def base_url() -> str:
        return _get_env("OPENAI_BASE_URL", "https://api.openai.com/v1")

    @staticmethod
    def default_model() -> str:
        return _get_env("OPENAI_MODEL", "gpt-4o")

    @staticmethod
    def thinking() -> str | None:
        value = _get_env("OPENAI_THINKING").strip().lower()
        if not value:
            return None
        if value not in {"enabled", "disabled"}:
            raise ValueError("OPENAI_THINKING must be 'enabled' or 'disabled'")
        return value

    @staticmethod
    def is_configured() -> bool:
        return bool(LLMConfig.api_key())


def create_gateway(
    model: Optional[str] = None,
    prompts_dir: Optional[str] = None,
    log_path: Optional[str] = None,
):
    from .gateway import LLMGateway
    from .prompts import PromptLoader
    from .providers import OpenAICompatibleProvider
    from .tracing import TraceLogger

    if not LLMConfig.is_configured():
        raise RuntimeError(
            "OPENAI_API_KEY not set. Create a .env file (see .env.example) "
            "or set the environment variable."
        )

    tracer_kwargs = {}
    if log_path:
        tracer_kwargs["log_path"] = log_path

    return LLMGateway(
        provider=OpenAICompatibleProvider(
            api_key=LLMConfig.api_key(),
            base_url=LLMConfig.base_url(),
            default_model=model or LLMConfig.default_model(),
            thinking=LLMConfig.thinking(),
        ),
        prompt_loader=PromptLoader(prompts_dir=prompts_dir),
        tracer=TraceLogger(**tracer_kwargs),
        default_model=model or LLMConfig.default_model(),
    )


class SharedGatewayFactory:
    """Create trace-isolated gateways backed by shared, long-lived clients."""

    def __init__(
        self,
        model: Optional[str] = None,
        prompts_dir: Optional[str] = None,
        log_path: Optional[str] = None,
    ) -> None:
        self.model = model
        self.prompts_dir = prompts_dir
        self.log_path = log_path
        self._components = None
        self._lock = threading.Lock()

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
        if not LLMConfig.is_configured():
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
                default_model = self.model or LLMConfig.default_model()
                self._components = (
                    OpenAICompatibleProvider(
                        api_key=LLMConfig.api_key(),
                        base_url=LLMConfig.base_url(),
                        default_model=default_model,
                        thinking=LLMConfig.thinking(),
                    ),
                    PromptLoader(prompts_dir=self.prompts_dir),
                    TraceLogger(**tracer_kwargs),
                    default_model,
                )
        return self._components
