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
    uow_factory: Callable[..., CampaignUnitOfWork] | None = field(
        default=None, compare=False
    )

    def uow(self, *, write: bool = False) -> CampaignUnitOfWork:
        """Return a fresh operation-level unit of work.

        The shared ``stores`` field is the session projection used by graph
        nodes that are already inside a pipeline-provided UoW. Callers that need
        an independent transaction boundary must create a fresh unit so writes
        do not share a connection with other operations.

        Phase 4: when a runtime factory is injected it is used so the UoW
        participates in quiesce/epoch guard.  Without one (bare tests), create
        a standalone UoW bound to this services' own paths.
        """
        if self.uow_factory is not None:
            return self.uow_factory(write=write)
        return CampaignUnitOfWork(self.stores.paths, write=write)

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
