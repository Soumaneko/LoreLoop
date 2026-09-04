import os
from dataclasses import dataclass
import threading
from typing import Literal, Optional

from app.core.diagnostics import DiagnosticLogger, diagnostic_path_for_trace
from app.runtime.environment import load_source_dotenv

load_source_dotenv()

Protocol = Literal[
    "openai_responses",
    "openai_chat",
    "anthropic_messages",
    "gemini",
]

PROTOCOLS: tuple[str, ...] = (
    "openai_responses",
    "openai_chat",
    "anthropic_messages",
    "gemini",
)

DEFAULT_PROTOCOL: Protocol = "openai_chat"


@dataclass(frozen=True)
class GatewayConfig:
    api_key: str
    # Empty means "let the configured protocol SDK use its official default".
    base_url: str = ""
    model: str = "gpt-4o"
    thinking: str | None = None
    protocol: Protocol = DEFAULT_PROTOCOL

    def __post_init__(self) -> None:
        api_key = str(self.api_key).strip()
        base_url = str(self.base_url).strip()
        model = str(self.model).strip()
        thinking = self.thinking
        protocol = str(self.protocol).strip()
        if thinking not in {None, "enabled", "disabled"}:
            raise ValueError("thinking must be 'enabled', 'disabled', or None")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol must be one of {', '.join(PROTOCOLS)}")
        if protocol != "openai_chat" and thinking == "enabled":
            raise ValueError(
                f"thinking is not supported for protocol '{protocol}' in this phase"
            )
        if not model:
            raise ValueError("model is required")
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "protocol", protocol)  # type: ignore[assignment]

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls, *, model: str | None = None) -> "GatewayConfig":
        thinking = os.environ.get("OPENAI_THINKING", "").strip().lower() or None
        protocol = os.environ.get("OPENAI_PROTOCOL", "").strip() or DEFAULT_PROTOCOL
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", "").strip(),
            model=model or os.environ.get("OPENAI_MODEL", "gpt-4o"),
            thinking=thinking,  # type: ignore[arg-type]
            protocol=protocol,  # type: ignore[arg-type]
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
    from .providers import PydanticAIProvider
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

    diagnostic_path = diagnostic_path_for_trace(log_path) if log_path else None
    diagnostics = DiagnosticLogger(diagnostic_path)
    return LLMGateway(
        provider=PydanticAIProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            default_model=config.model,
            thinking=config.thinking,
            protocol=config.protocol,
        ),
        prompt_loader=PromptLoader(prompts_dir=prompts_dir),
        tracer=TraceLogger(**tracer_kwargs, diagnostics=diagnostics),
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
        diagnostic_path: str | None = None,
    ) -> None:
        self.config = config or GatewayConfig.from_env(model=model)
        self.model = self.config.model
        self.prompts_dir = prompts_dir
        self.log_path = log_path
        if diagnostic_path is None and log_path is not None:
            diagnostic_path = str(diagnostic_path_for_trace(log_path))
        self.diagnostic_logger = DiagnosticLogger(diagnostic_path)
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
        from .providers import PydanticAIProvider
        from .tracing import TraceLogger

        with self._lock:
            if self._components is None:
                tracer_kwargs = {}
                if self.log_path:
                    tracer_kwargs["log_path"] = self.log_path
                default_model = self.config.model
                self._components = (
                    PydanticAIProvider(
                        api_key=self.config.api_key,
                        base_url=self.config.base_url,
                        default_model=default_model,
                        thinking=self.config.thinking,
                        protocol=self.config.protocol,
                    ),
                    PromptLoader(prompts_dir=self.prompts_dir),
                    TraceLogger(
                        **tracer_kwargs,
                        diagnostics=self.diagnostic_logger,
                    ),
                    default_model,
                )
        return self._components
