from pathlib import Path

import pytest

from app.core.llm.errors import PromptNotFoundError, PromptRenderError
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

        shared_markers = [
            "# 权威上下文",
            "# 输出规范",
            "# 共同创作契约",
            "严禁替玩家作出重大不可逆决定",
            "CoreMemory、CurrentState、ActionJudgments、Director Agent",
        ]
        for prompt in (compose, source, recompose):
            assert "[[include:" not in prompt
            for marker in shared_markers:
                assert prompt.count(marker) == 1

        assert "Source Scene Text" not in compose
        assert "Source Scene Text" in source
        assert "Source Scene Text" not in recompose
        assert "Rewrite Requirements" not in compose
        assert "Rewrite Requirements" in source
        assert "Recompose Requirements" in recompose

    def test_prompt_loader_caches_expanded_content(self, tmp_path):
        (tmp_path / "cached_v1.md").write_text("Hello {name}", encoding="utf-8")
        loader = PromptLoader(prompts_dir=tmp_path)

        first_content, first_version = loader.load("cached")
        stats_after_first = loader.get_cache_stats()
        assert stats_after_first["misses"] == 1
        assert stats_after_first["hits"] == 0
        assert stats_after_first["template_cache_size"] == 1

        second_content, second_version = loader.load("cached")
        stats_after_second = loader.get_cache_stats()
        assert stats_after_second["misses"] == 1
        assert stats_after_second["hits"] == 1
        assert stats_after_second["template_cache_size"] == 1

        assert first_content == second_content == "Hello {name}"
        assert first_version == second_version == "v1"

    def test_prompt_loader_warm_load_reads_zero_file_content(self, tmp_path, monkeypatch):
        shared = tmp_path / "shared.txt"
        shared.write_text("SHARED", encoding="utf-8")
        (tmp_path / "warm_v1.md").write_text("Main\n[[include: shared.txt]]", encoding="utf-8")
        loader = PromptLoader(prompts_dir=tmp_path)

        # Cold load
        res1, _ = loader.load("warm")
        assert res1 == "Main\nSHARED"

        # Intercept read_text during warm load
        read_count = [0]
        orig_read_text = Path.read_text

        def _counted_read_text(self, *args, **kwargs):
            read_count[0] += 1
            return orig_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _counted_read_text)

        res2, _ = loader.load("warm")
        assert res2 == "Main\nSHARED"
        assert read_count[0] == 0  # 0 file content reads on warm cache!
        assert loader.get_cache_stats()["hits"] == 1

    def test_prompt_loader_hot_reloads_on_main_file_change(self, tmp_path):
        import time

        main_file = tmp_path / "reload_v1.md"
        main_file.write_text("Initial text", encoding="utf-8")
        loader = PromptLoader(prompts_dir=tmp_path)

        assert loader.load("reload")[0] == "Initial text"

        time.sleep(0.01)
        main_file.write_text("Updated text", encoding="utf-8")

        assert loader.load("reload")[0] == "Updated text"
        stats = loader.get_cache_stats()
        assert stats["reloads"] == 1

    def test_prompt_loader_hot_reloads_on_direct_include_change(self, tmp_path):
        import time

        inc_file = tmp_path / "inc.txt"
        inc_file.write_text("Inc V1", encoding="utf-8")
        (tmp_path / "direct_v1.md").write_text("Head\n[[include: inc.txt]]\nTail", encoding="utf-8")
        loader = PromptLoader(prompts_dir=tmp_path)

        assert loader.load("direct")[0] == "Head\nInc V1\nTail"

        time.sleep(0.01)
        inc_file.write_text("Inc V2", encoding="utf-8")

        assert loader.load("direct")[0] == "Head\nInc V2\nTail"
        assert loader.get_cache_stats()["reloads"] == 1

    def test_prompt_loader_hot_reloads_on_transitive_include_change(self, tmp_path):
        import time

        b_file = tmp_path / "b.txt"
        b_file.write_text("B1", encoding="utf-8")
        (tmp_path / "a.txt").write_text("A\n[[include: b.txt]]", encoding="utf-8")
        (tmp_path / "chain_v1.md").write_text("Chain\n[[include: a.txt]]", encoding="utf-8")
        loader = PromptLoader(prompts_dir=tmp_path)

        assert loader.load("chain")[0] == "Chain\nA\nB1"

        time.sleep(0.01)
        b_file.write_text("B2", encoding="utf-8")

        assert loader.load("chain")[0] == "Chain\nA\nB2"
        assert loader.get_cache_stats()["reloads"] == 1

    def test_prompt_loader_hot_reloads_on_non_max_mtime_change(self, tmp_path):
        import os

        a_file = tmp_path / "a.txt"
        b_file = tmp_path / "b.txt"
        main_file = tmp_path / "nonmax_v1.md"

        main_file.write_text("M\n[[include: a.txt]]\n[[include: b.txt]]", encoding="utf-8")
        a_file.write_text("A1", encoding="utf-8")
        b_file.write_text("B1", encoding="utf-8")

        # Set a_file mtime to future (e.g. 5000) and b_file to 1000
        os.utime(a_file, (5000, 5000))
        os.utime(b_file, (1000, 1000))
        os.utime(main_file, (1000, 1000))

        loader = PromptLoader(prompts_dir=tmp_path)
        assert loader.load("nonmax")[0] == "M\nA1\nB1"

        # Update b_file to mtime 2000 (still smaller than a_file's 5000!)
        b_file.write_text("B2", encoding="utf-8")
        os.utime(b_file, (2000, 2000))

        # Must detect invalidation and reload
        assert loader.load("nonmax")[0] == "M\nA1\nB2"
        assert loader.get_cache_stats()["reloads"] == 1

    def test_prompt_loader_invalidates_on_dependency_deletion(self, tmp_path):
        inc_file = tmp_path / "to_delete.txt"
        inc_file.write_text("Present", encoding="utf-8")
        (tmp_path / "deldep_v1.md").write_text("Base\n[[include: to_delete.txt]]", encoding="utf-8")

        loader = PromptLoader(prompts_dir=tmp_path)
        assert loader.load("deldep")[0] == "Base\nPresent"

        # Delete include file
        inc_file.unlink()

        # Second load must invalidate cache and raise PromptNotFoundError
        with pytest.raises(PromptNotFoundError, match="Prompt include not found"):
            loader.load("deldep")

    def test_prompt_loader_duplicate_filename_detection(self, tmp_path):
        sub1 = tmp_path / "sub1"
        sub2 = tmp_path / "sub2"
        sub1.mkdir()
        sub2.mkdir()
        (sub1 / "dup_v1.md").write_text("Dup 1", encoding="utf-8")
        (sub2 / "dup_v1.md").write_text("Dup 2", encoding="utf-8")

        loader = PromptLoader(prompts_dir=tmp_path)
        with pytest.raises(ValueError, match="Duplicate prompt filename found in prompts directory"):
            loader.load("dup")

    def test_prompt_loader_clear_cache(self, tmp_path):
        (tmp_path / "clear_v1.md").write_text("Clear me", encoding="utf-8")
        loader = PromptLoader(prompts_dir=tmp_path)

        loader.load("clear")
        assert loader.get_cache_stats()["hits"] == 0
        assert loader.get_cache_stats()["misses"] == 1

        loader.clear_cache()
        stats = loader.get_cache_stats()
        assert stats["index_size"] == 0
        assert stats["template_cache_size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

        # Reload is a cold miss again
        loader.load("clear")
        assert loader.get_cache_stats()["misses"] == 1

    def test_prompt_loader_thread_safety(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        shared = tmp_path / "shared.txt"
        shared.write_text("SHARED_VAL", encoding="utf-8")
        for i in range(5):
            (tmp_path / f"thread_{i}_v1.md").write_text(f"Task {i}\n[[include: shared.txt]]", encoding="utf-8")

        loader = PromptLoader(prompts_dir=tmp_path)

        def _worker(task_id):
            content, version = loader.load(f"thread_{task_id}")
            assert content == f"Task {task_id}\nSHARED_VAL"
            assert version == "v1"

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_worker, i % 5) for i in range(50)]
            for f in futures:
                f.result()

        stats = loader.get_cache_stats()
        assert stats["misses"] == 5
        assert stats["hits"] == 45
        assert stats["template_cache_size"] == 5

    def test_prompt_loader_discovers_dynamically_added_file_without_restart(self, tmp_path):
        (tmp_path / "initial_v1.md").write_text("Initial prompt", encoding="utf-8")
        loader = PromptLoader(prompts_dir=tmp_path)

        # First load builds the index
        assert loader.load("initial")[0] == "Initial prompt"
        assert "dynamic" not in loader.list_tasks()

        # Dynamically create a new prompt file at runtime
        (tmp_path / "dynamic_v1.md").write_text("Dynamically added prompt", encoding="utf-8")

        # Must automatically discover the new file without requiring clear_cache()
        content, version = loader.load("dynamic")
        assert content == "Dynamically added prompt"
        assert version == "v1"
        assert "dynamic" in loader.list_tasks()
