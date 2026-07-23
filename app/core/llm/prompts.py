import os
import re
from pathlib import Path

from .errors import PromptNotFoundError


_INCLUDE_PATTERN = re.compile(
    r"^\[\[include:\s*([^\]\r\n]+?)\s*\]\]$",
    re.MULTILINE,
)


class PromptLoader:
    def __init__(self, prompts_dir: str | None = None):
        if prompts_dir is None:
            prompts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
        self._prompts_dir = Path(prompts_dir)

    @property
    def prompts_dir(self) -> Path:
        return self._prompts_dir

    def _find_file(self, filename: str) -> Path | None:
        for f in self._prompts_dir.rglob(filename):
            return f
        return None

    def load(self, task: str, version: str = "v1") -> tuple[str, str]:
        filename = f"{task}_{version}.md"
        filepath = self._find_file(filename)

        if filepath is None:
            raise PromptNotFoundError(
                f"Prompt file not found: {self._prompts_dir / filename} (task={task}, version={version})"
            )

        content = self._expand_includes(filepath, stack=())
        return content, version

    def _expand_includes(
        self,
        filepath: Path,
        *,
        stack: tuple[Path, ...],
    ) -> str:
        root = self._prompts_dir.resolve()
        resolved = filepath.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Prompt include escapes prompts directory: {filepath}")
        if resolved in stack:
            chain = " -> ".join(path.name for path in (*stack, resolved))
            raise ValueError(f"Circular prompt include: {chain}")
        if not resolved.is_file():
            raise PromptNotFoundError(f"Prompt include not found: {filepath}")

        content = resolved.read_text(encoding="utf-8")
        next_stack = (*stack, resolved)

        def replace(match: re.Match[str]) -> str:
            relative = Path(match.group(1).strip())
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid prompt include path: {relative}")
            included = root / relative
            return self._expand_includes(included, stack=next_stack).rstrip()

        return _INCLUDE_PATTERN.sub(replace, content)

    def list_tasks(self) -> list[str]:
        tasks = set()
        for f in self._prompts_dir.rglob("*.md"):
            name = f.stem
            parts = name.rsplit("_", 1)
            if len(parts) == 2 and parts[1].startswith("v"):
                tasks.add(parts[0])
        return sorted(tasks)
