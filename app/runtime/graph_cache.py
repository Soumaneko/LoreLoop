from __future__ import annotations

import threading
from typing import Any, Callable


class CompiledGraphCache:
    """Cache one compiled graph per name and dependency identity set."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._graphs: dict[str, tuple[tuple[int, ...], Any]] = {}

    def get_or_build(
        self,
        name: str,
        dependencies: tuple[Any, ...],
        builder: Callable[[], Any],
    ) -> Any:
        signature = tuple(id(dependency) for dependency in dependencies)
        with self._lock:
            cached = self._graphs.get(name)
            if cached is not None and cached[0] == signature:
                return cached[1]
            graph = builder()
            self._graphs[name] = (signature, graph)
            return graph

    def clear(self, name: str | None = None) -> None:
        with self._lock:
            if name is None:
                self._graphs.clear()
            else:
                self._graphs.pop(name, None)


COMPILED_GRAPH_CACHE = CompiledGraphCache()


def get_cached_graph(
    name: str,
    dependencies: tuple[Any, ...],
    builder: Callable[[], Any],
) -> Any:
    return COMPILED_GRAPH_CACHE.get_or_build(name, dependencies, builder)
