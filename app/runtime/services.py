from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.diagnostics import DiagnosticLogger, NullDiagnosticLogger
from app.runtime.uow import CampaignUnitOfWork


@dataclass(frozen=True)
class RuntimeServices:
    """Dependencies shared by graph nodes within one campaign runtime."""

    stores: CampaignUnitOfWork
    gateway_factory: Callable[[], Any]
    jobs: Any
    diagnostics: DiagnosticLogger | NullDiagnosticLogger = field(
        default_factory=NullDiagnosticLogger
    )

    def uow(self, *, write: bool = False) -> CampaignUnitOfWork:
        """Return the campaign unit of work bound to this runtime.

        In production ``stores`` is a :class:`CampaignUnitOfWork`; under tests it
        may be a lightweight namespace of store instances. Both expose the same
        ``current_state`` / ``npc_profile`` / ... accessors the nodes rely on.
        """
        return self.stores

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
