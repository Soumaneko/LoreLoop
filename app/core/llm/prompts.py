import os
from pathlib import Path

from .errors import PromptNotFoundError


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

        content = filepath.read_text(encoding="utf-8")
        return content, version

    def list_tasks(self) -> list[str]:
        tasks = set()
        for f in self._prompts_dir.rglob("*.md"):
            name = f.stem
            parts = name.rsplit("_", 1)
            if len(parts) == 2 and parts[1].startswith("v"):
                tasks.add(parts[0])
        return sorted(tasks)
