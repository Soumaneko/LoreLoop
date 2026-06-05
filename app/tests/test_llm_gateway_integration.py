import os
import tempfile

import pytest

from app.core.llm.config import LLMConfig, create_gateway
from app.core.llm.schemas import EchoResult

pytestmark = pytest.mark.skipif(
    not LLMConfig.is_configured(),
    reason="OPENAI_API_KEY not set. Skipping integration tests.",
)


def _load_env():
    from dotenv import load_dotenv
    load_dotenv()


class TestEchoStructuredWithRealLLM:
    def test_echo_hello(self):
        gateway = create_gateway()

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
        )

        assert isinstance(result, EchoResult)
        assert result.text == "hello"
        assert result.length == 5

    def test_echo_chinese_text(self):
        gateway = create_gateway()

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "你好世界"},
            output_schema=EchoResult,
        )

        assert isinstance(result, EchoResult)
        assert result.text == "你好世界"
        assert result.length == 4

    def test_echo_empty_string(self):
        gateway = create_gateway()

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": ""},
            output_schema=EchoResult,
        )

        assert isinstance(result, EchoResult)
        assert result.text == ""
        assert result.length == 0


class TestTracingWithRealLLM:
    def test_trace_written_with_real_llm(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            trace_path = f.name

        try:
            gateway = create_gateway(log_path=trace_path)

            gateway.run(
                task="echo_structured_test",
                input_data={"text": "trace test"},
                output_schema=EchoResult,
                trace_id="real-llm-trace-1",
            )

            traces = gateway.tracer.read_traces(trace_id="real-llm-trace-1")
            assert len(traces) == 1
            trace = traces[0]
            assert trace["trace_id"] == "real-llm-trace-1"
            assert trace["schema_valid"] is True
            assert trace["error"] is None
            assert isinstance(trace["latency_ms"], float)
            assert trace["latency_ms"] > 0
        finally:
            os.unlink(trace_path)


class TestMemoryExtractWithRealLLM:
    def test_memory_extract_placeholder(self):
        gateway = create_gateway()

        result = gateway.run(
            task="memory_extract",
            input_data={"text": "User mentioned they like coffee."},
            output_schema=EchoResult,
        )

        assert isinstance(result, EchoResult)
