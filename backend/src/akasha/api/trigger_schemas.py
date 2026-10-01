"""Response schemas for trigger/execution endpoints (boundary models)."""

from datetime import datetime

from pydantic import BaseModel

from akasha.application.persistence import ExecutionSummary
from akasha.domain.execution import Execution
from akasha.domain.trigger import TriggerType


class ExecutionDetail(BaseModel):
    """The canonical execution representation: what every trigger returns
    and what execution listings contain. Node-level detail is the report."""

    id: str
    project_id: str
    version: int
    status: str
    trigger_type: TriggerType
    trigger_id: str | None
    correlation_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_domain(cls, execution: Execution) -> "ExecutionDetail":
        return cls(
            id=execution.id,
            project_id=execution.project_id,
            version=execution.project_version.version,
            status=execution.status.value,
            trigger_type=execution.metadata.trigger_type,
            trigger_id=execution.metadata.trigger_id,
            correlation_id=execution.metadata.correlation_id,
            created_at=execution.created_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
        )

    @classmethod
    def from_summary(cls, summary: ExecutionSummary) -> "ExecutionDetail":
        return cls(
            id=summary.id,
            project_id=summary.project_id,
            version=summary.project_version,
            status=summary.status.value,
            trigger_type=summary.metadata.trigger_type,
            trigger_id=summary.metadata.trigger_id,
            correlation_id=summary.metadata.correlation_id,
            created_at=summary.created_at,
            started_at=summary.started_at,
            finished_at=summary.finished_at,
        )
