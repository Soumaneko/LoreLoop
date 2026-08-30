from pathlib import Path

import pytest

from app.core.llm.errors import PromptRenderError
from app.core.llm.gateway import LLMGateway
from app.core.llm.mock import MockLLM
from app.core.llm.providers import MockProvider
from app.core.llm.prompts import PromptLoader
from app.core.llm.schemas import EchoResult
from app.core.llm.tracing import NullTraceLogger


class TestPromptRendering:
    def test_missing_template_variable_raises_specific_error(self):
        gateway = LLMGateway(
            provider=MockProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )

        with pytest.raises(
            PromptRenderError,
            match="Missing prompt template variable: text",
        ):
            gateway.run(
                task="echo_structured_test",
                input_data={},
                output_schema=EchoResult,
            )

    def test_json_braces_inside_input_value_are_not_reinterpreted(self):
        gateway = LLMGateway(
            provider=MockProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )

        assert gateway._build_prompt(
            "Payload: {payload}",
            {"payload": '{"nested": "{not_a_template_variable}"}'},
        ) == 'Payload: {"nested": "{not_a_template_variable}"}'

    def test_invalid_diagnostic_severity_is_rejected(self):
        gateway = LLMGateway(
            provider=MockProvider(),
            prompt_loader=MockLLM().prompt_loader,
            tracer=NullTraceLogger(),
        )

        with pytest.raises(ValueError, match="Unsupported diagnostic severity"):
            gateway._log_diagnostic(
                severity="warn",  # type: ignore[arg-type]
                code="test",
                message="test",
                trace_id="trace",
                task="task",
                attempt=1,
            )


class TestPromptLoader:
    def test_load_valid_prompt(self):
        loader = PromptLoader()
        content, version = loader.load("echo_structured_test")
        assert isinstance(content, str)
        assert len(content) > 0
        assert version == "v1"
        assert "{text}" in content

    def test_load_with_explicit_version(self):
        loader = PromptLoader()
        content, version = loader.load("echo_structured_test", version="v1")
        assert version == "v1"
        assert "{text}" in content

    def test_list_tasks(self):
        loader = PromptLoader()
        tasks = loader.list_tasks()
        assert "echo_structured_test" in tasks
        assert "continuity_project" in tasks

    def test_expands_root_relative_prompt_include(self, tmp_path):
        shared = tmp_path / "_shared"
        shared.mkdir()
        (shared / "contract.txt").write_text(
            "Shared {value}",
            encoding="utf-8",
        )
        (tmp_path / "composed_v1.md").write_text(
            "Before\n[[include: _shared/contract.txt]]\nAfter",
            encoding="utf-8",
        )

        content, _ = PromptLoader(prompts_dir=tmp_path).load("composed")

        assert content == "Before\nShared {value}\nAfter"

    @pytest.mark.parametrize(
        "include_path",
        ["/tmp/outside.txt", r"C:\tmp\outside.txt", "../outside.txt"],
    )
    def test_rejects_prompt_include_outside_root(self, tmp_path, include_path):
        (tmp_path / "composed_v1.md").write_text(
            f"[[include: {include_path}]]",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Invalid prompt include path"):
            PromptLoader(prompts_dir=tmp_path).load("composed")

    def test_rejects_circular_prompt_include(self, tmp_path):
        (tmp_path / "composed_v1.md").write_text(
            "[[include: shared.txt]]",
            encoding="utf-8",
        )
        (tmp_path / "shared.txt").write_text(
            "[[include: composed_v1.md]]",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Circular prompt include"):
            PromptLoader(prompts_dir=tmp_path).load("composed")

    def test_narrative_prompt_modes_share_contract_without_leaking_inputs(self):
        loader = PromptLoader()
        compose, _ = loader.load("narrative_compose")
        source, _ = loader.load("narrative_rewrite_from_source")
        recompose, _ = loader.load("narrative_recompose")

        for prompt in (compose, source, recompose):
            assert "[[include:" not in prompt
        # Modes must be distinct (no input leakage via shared template)
        assert compose != source
        assert source != recompose
        assert compose != recompose

    def test_prompt_loader_invalidates_when_include_changes(self, tmp_path):
        (tmp_path / "shared.txt").write_text("A", encoding="utf-8")
        (tmp_path / "cached_v1.md").write_text(
            "[[include: shared.txt]]",
            encoding="utf-8",
        )
        loader = PromptLoader(prompts_dir=tmp_path)

        assert loader.load("cached")[0] == "A"
        (tmp_path / "shared.txt").write_text("Updated", encoding="utf-8")

        assert loader.load("cached")[0] == "Updated"
        assert loader.get_cache_stats()["reloads"] == 1
