from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.runtime.stores import StoreBundle


@dataclass(frozen=True)
class RuntimeServices:
    """Dependencies shared by graph nodes within one campaign runtime."""

    stores: StoreBundle
    gateway_factory: Callable[[], Any]
    jobs: Any

    def gateway(
        self,
        *,
        session_type: str,
        turn_index: int | None = None,
    ) -> Any:
        gateway = self.gateway_factory()
        context: dict[str, Any] = {"session_type": session_type}
        if turn_index is not None:
            context["turn_index"] = turn_index
        gateway.set_trace_context(**context)
        return gateway
