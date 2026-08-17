from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import threading

from app.runtime.app_paths import APP_PATHS

from .errors import PromptNotFoundError


_INCLUDE_PATTERN = re.compile(
    r"^\[\[include:\s*([^\]\r\n]+?)\s*\]\]$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class _FileStamp:
    mtime_ns: int
    size: int


@dataclass
class _CachedPrompt:
    content: str
    version: str
    dependencies: dict[Path, _FileStamp]


class PromptLoader:
    def __init__(self, prompts_dir: str | None = None):
        if prompts_dir is None:
            prompts_dir = str(APP_PATHS.prompts_dir)
        self._prompts_dir = Path(prompts_dir)
        self._lock = threading.RLock()
        self._file_index: dict[str, Path] | None = None
        self._template_cache: dict[Path, _CachedPrompt] = {}
        self._hits = 0
        self._misses = 0
        self._reloads = 0

    @property
    def prompts_dir(self) -> Path:
        return self._prompts_dir

    def clear_cache(self) -> None:
        with self._lock:
            self._file_index = None
            self._template_cache.clear()
            self._hits = 0
            self._misses = 0
            self._reloads = 0

    def get_cache_stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "index_size": len(self._file_index) if self._file_index is not None else 0,
                "template_cache_size": len(self._template_cache),
                "hits": self._hits,
                "misses": self._misses,
                "reloads": self._reloads,
            }

    def _scan_files(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        if self._prompts_dir.exists():
            for filepath in self._prompts_dir.rglob("*"):
                if filepath.is_file():
                    name = filepath.name
                    if name in index:
                        existing = index[name]
                        if existing != filepath:
                            raise ValueError(
                                f"Duplicate prompt filename found in prompts directory: "
                                f"'{name}' at both '{existing}' and '{filepath}'"
                            )
                    index[name] = filepath
        return index

    def _ensure_file_index(self) -> dict[str, Path]:
        if self._file_index is None:
            self._file_index = self._scan_files()
        return self._file_index

    def _find_file(self, filename: str) -> Path | None:
        index = self._ensure_file_index()
        if filename in index:
            return index[filename]
        # Fallback rescan when looking for a dynamically added prompt file
        self._file_index = self._scan_files()
        return self._file_index.get(filename)

    def load(self, task: str, version: str = "v1") -> tuple[str, str]:
        with self._lock:
            filename = f"{task}_{version}.md"
            filepath = self._find_file(filename)

            if filepath is None:
                raise PromptNotFoundError(
                    f"Prompt file not found: {self._prompts_dir / filename} (task={task}, version={version})"
                )

            cached = self._template_cache.get(filepath)
            if cached is not None:
                is_valid = True
                for dep_path, old_stamp in cached.dependencies.items():
                    try:
                        stat = dep_path.stat()
                        if (stat.st_mtime_ns, stat.st_size) != (
                            old_stamp.mtime_ns,
                            old_stamp.size,
                        ):
                            is_valid = False
                            break
                    except (OSError, ValueError):
                        is_valid = False
                        break

                if is_valid:
                    self._hits += 1
                    return cached.content, cached.version
                else:
                    self._reloads += 1
            else:
                self._misses += 1

            deps: dict[Path, _FileStamp] = {}
            content = self._expand_includes(filepath, stack=(), collected_deps=deps)
            self._template_cache[filepath] = _CachedPrompt(
                content=content,
                version=version,
                dependencies=deps,
            )
            return content, version

    def _expand_includes(
        self,
        filepath: Path,
        *,
        stack: tuple[Path, ...],
        collected_deps: dict[Path, _FileStamp] | None = None,
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

        stat = resolved.stat()
        if collected_deps is not None:
            collected_deps[resolved] = _FileStamp(
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
            )

        content = resolved.read_text(encoding="utf-8")
        next_stack = (*stack, resolved)

        def replace(match: re.Match[str]) -> str:
            value = match.group(1).strip()
            posix_path = PurePosixPath(value)
            windows_path = PureWindowsPath(value)
            if (
                "\\" in value
                or posix_path.is_absolute()
                or windows_path.is_absolute()
                or ".." in posix_path.parts
            ):
                raise ValueError(f"Invalid prompt include path: {value}")
            relative = Path(*posix_path.parts)
            included = root / relative
            return self._expand_includes(
                included,
                stack=next_stack,
                collected_deps=collected_deps,
            ).rstrip()

        return _INCLUDE_PATTERN.sub(replace, content)

    def list_tasks(self) -> list[str]:
        with self._lock:
            index = self._ensure_file_index()
            tasks = set()
            for name in index:
                if name.endswith(".md"):
                    parts = Path(name).stem.rsplit("_", 1)
                    if len(parts) == 2 and parts[1].startswith("v"):
                        tasks.add(parts[0])
            return sorted(tasks)
