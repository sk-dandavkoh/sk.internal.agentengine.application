"""Response schemas for trigger/execution endpoints (boundary models)."""

from datetime import datetime

from pydantic import BaseModel

from akasha.application.persistence import ExecutionSummary
from akasha.domain.execution import EXECUTION_ACTIONS, Execution, ExecutionStatus
from akasha.domain.trigger import TriggerType


def _cancellable(status: ExecutionStatus) -> bool:
    return status in EXECUTION_ACTIONS["cancel"][0]


class ExecutionDetail(BaseModel):
    """The canonical execution representation: what every trigger returns
    and what execution listings contain. Node-level detail is the report."""

    id: str
    project_id: str
    version: int
    status: ExecutionStatus
    trigger_type: TriggerType
    trigger_id: str | None
    correlation_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # The state machine's answer, so clients offer cancel only where
    # POST /executions/{id}/cancel can succeed (they never re-derive it).
    cancellable: bool

    @classmethod
    def from_domain(cls, execution: Execution) -> "ExecutionDetail":
        return cls(
            id=execution.id,
            project_id=execution.project_id,
            version=execution.project_version.version,
            status=execution.status,
            trigger_type=execution.metadata.trigger_type,
            trigger_id=execution.metadata.trigger_id,
            correlation_id=execution.metadata.correlation_id,
            created_at=execution.created_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            cancellable=_cancellable(execution.status),
        )


class ExecutionListItem(ExecutionDetail):
    """An execution in a listing: the canonical representation plus its
    project's current name, joined in the same query. Single-execution
    responses omit it; the public webhook response must not disclose it."""

    project_name: str

    @classmethod
    def from_summary(cls, summary: ExecutionSummary) -> "ExecutionListItem":
        return cls(
            id=summary.id,
            project_id=summary.project_id,
            project_name=summary.project_name,
            version=summary.project_version,
            status=summary.status,
            trigger_type=summary.metadata.trigger_type,
            trigger_id=summary.metadata.trigger_id,
            correlation_id=summary.metadata.correlation_id,
            created_at=summary.created_at,
            started_at=summary.started_at,
            finished_at=summary.finished_at,
            cancellable=_cancellable(summary.status),
        )
