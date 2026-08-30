import pytest

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from app.core.llm.errors import LLMOutputValidationError
from app.core.llm.gateway import LLMGateway
from app.core.llm.mock import MockLLM
from app.core.llm.providers import MockProvider
from app.core.llm.tracing import NullTraceLogger
from app.core.llm.tracing import TraceLogger


class _RecordingProvider(MockProvider):
    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    def generate(self, prompt, *, model="", temperature=0.2):
        self.prompts.append(prompt)
        return super().generate(prompt, model=model, temperature=temperature)


class _ForbidMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    context_span: str


class _ForbidMentions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[_ForbidMention] = Field(default_factory=list)


class TestSchemaCopyDefense:
    def _recording_gateway(
        self, responses: list[str]
    ) -> tuple[LLMGateway, _RecordingProvider]:
        provider = _RecordingProvider()
        provider.set_responses(responses)
        gateway = LLMGateway(
            provider=provider,
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )
        return gateway, provider

    def _run_mentions(
        self,
        gateway: LLMGateway,
        responses: list[str],
        *,
        max_retries: int = 2,
    ):
        return gateway.run(
            task="echo_structured_test",
            input_data={"text": "x", "output_schema_json": "{}"},
            output_schema=_ForbidMentions,
            max_retries=max_retries,
        )



    def test_salvages_root_data_next_to_schema_scaffolding(self):
        gateway, provider = self._recording_gateway(
            [
                '{"$defs":{},"type":"object",'
                '"properties":{"mentions":{"items":{},"type":"array"}},'
                '"mentions":[{"name":"萧欢","context_span":"萧欢在醉花楼。"}]}'
            ]
        )

        result = self._run_mentions(gateway, provider.prompts)

        assert result.mentions[0].name == "萧欢"
        assert result.mentions[0].context_span == "萧欢在醉花楼。"
        assert len(provider.prompts) == 1

    def test_salvages_data_nested_in_properties(self):
        gateway, provider = self._recording_gateway(
            [
                '{"type":"object",'
                '"properties":{"mentions":[{"name":"萧欢","context_span":"萧欢在醉花楼。"}]}}'
            ]
        )

        result = self._run_mentions(gateway, provider.prompts)

        assert result.mentions[0].name == "萧欢"
        assert result.mentions[0].context_span == "萧欢在醉花楼。"
        assert len(provider.prompts) == 1

    def test_pure_schema_copy_fails_after_retries(self):
        schema_copy = (
            '{"$defs":{},"type":"object",'
            '"properties":{"mentions":{"items":{"$ref":"#/$defs/M"},"type":"array"}}}'
        )
        gateway, provider = self._recording_gateway([schema_copy] * 3)

        with pytest.raises(LLMOutputValidationError):
            self._run_mentions(gateway, provider.prompts)

        assert len(provider.prompts) == 3

    def test_retry_prompt_teaches_schema_copy_when_salvage_fails(self):
        schema_copy = (
            '{"$defs":{},"type":"object",'
            '"properties":{"mentions":{"items":{"$ref":"#/$defs/M"},"type":"array"}}}'
        )
        gateway, provider = self._recording_gateway(
            [
                schema_copy,
                '{"mentions":[{"name":"萧欢","context_span":"萧欢在醉花楼。"}]}',
            ]
        )

        result = self._run_mentions(gateway, provider.prompts)

        assert result.mentions[0].name == "萧欢"
        assert result.mentions[0].context_span == "萧欢在醉花楼。"
        assert len(provider.prompts) == 2

    def test_normal_output_without_signature_unchanged(self):
        gateway, provider = self._recording_gateway(
            ['{"mentions":[{"name":"萧欢","context_span":"萧欢在醉花楼。"}]}']
        )

        result = self._run_mentions(gateway, provider.prompts)

        assert result.mentions[0].name == "萧欢"
        assert result.mentions[0].context_span == "萧欢在醉花楼。"
        assert len(provider.prompts) == 1

    def test_trace_records_salvaged_output_not_schema_wrapper(self, tmp_path):
        provider = _RecordingProvider()
        provider.set_responses(
            [
                '{"$defs":{},"type":"object",'
                '"properties":{"mentions":{"items":{},"type":"array"}},'
                '"mentions":[{"name":"萧欢","context_span":"萧欢在醉花楼。"}]}'
            ]
        )
        trace_path = tmp_path / "trace.jsonl"
        tracer = TraceLogger(log_path=str(trace_path))
        gateway = LLMGateway(
            provider=provider,
            prompt_loader=MockLLM().prompt_loader,
            tracer=tracer,
        )

        self._run_mentions(gateway, provider.prompts)

        traces = tracer.read_traces()
        assert len(traces) == 1
        assert traces[0]["schema_valid"] is True
        assert traces[0]["parsed_output"] == {
            "mentions": [{"name": "萧欢", "context_span": "萧欢在醉花楼。"}]
        }
