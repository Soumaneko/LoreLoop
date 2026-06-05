import os
from functools import lru_cache
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
        ),
        prompt_loader=PromptLoader(prompts_dir=prompts_dir),
        tracer=TraceLogger(**tracer_kwargs),
        default_model=model or LLMConfig.default_model(),
    )
