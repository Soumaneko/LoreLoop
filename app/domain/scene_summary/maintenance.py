from dataclasses import dataclass, field
from threading import Lock

from app.domain.scene_summary.schemas import SceneSummary


_CONDENSE_LOCK = Lock()


@dataclass
class SceneSummaryMaintenanceResult:
    saved_l1_id: str | None = None
    condensed_ids: list[str] = field(default_factory=list)
    created_summaries: list[SceneSummary] = field(default_factory=list)
