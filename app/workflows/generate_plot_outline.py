from app.core.llm import LLMGateway
from app.domain.plot_outline.agent import PlotOutlineAgent
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.workflows.context_assembler import PlotOutlineContext


def generate_plot_outline_workflow(
    context: PlotOutlineContext,
    gateway: LLMGateway,
    *,
    trace_id: str | None = None,
) -> PlotOutlineResult:
    agent = PlotOutlineAgent(gateway)
    return agent.generate(context, trace_id=trace_id)
