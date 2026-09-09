class LLMError(Exception):
    pass


class LLMProviderError(LLMError):
    pass


class LLMTimeoutError(LLMProviderError):
    """A single logical provider call exceeded the project timeout."""

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
        model: str | None = None,
        protocol: str | None = None,
        phase: str | None = None,
        transport_attempts: int | None = None,
        task: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.protocol = protocol
        self.phase = phase
        self.transport_attempts = transport_attempts
        self.task = task
        self.elapsed_ms = elapsed_ms


class LLMOutputParseError(LLMError):
    pass


class LLMOutputValidationError(LLMError):
    pass


class PromptNotFoundError(LLMError):
    pass


class PromptRenderError(LLMError):
    pass
