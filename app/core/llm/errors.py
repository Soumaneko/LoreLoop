class LLMError(Exception):
    pass


class LLMProviderError(LLMError):
    pass


class LLMOutputParseError(LLMError):
    pass


class LLMOutputValidationError(LLMError):
    pass


class PromptNotFoundError(LLMError):
    pass


class PromptRenderError(LLMError):
    pass
