class MockLLM:
    def __init__(self):
        from .providers import MockProvider
        from .prompts import PromptLoader

        self._provider = MockProvider()
        self._prompt_loader = PromptLoader()

    @property
    def provider(self):
        return self._provider

    @property
    def prompt_loader(self):
        return self._prompt_loader

    def set_responses(self, responses: list[str]) -> None:
        self._provider.set_responses(responses)

    def set_fail_provider(self, value: bool = True) -> None:
        self._provider.set_fail_provider(value)

    def set_timeout(self, value: bool = True) -> None:
        self._provider.set_timeout(value)

    def build_gateway(self):
        from .gateway import LLMGateway
        from .tracing import NullTraceLogger

        return LLMGateway(
            provider=self._provider,
            prompt_loader=self._prompt_loader,
            tracer=NullTraceLogger(),
        )
