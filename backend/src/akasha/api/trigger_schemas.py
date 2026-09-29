"""Response schemas for trigger/execution endpoints (boundary models)."""

from datetime import datetime

from pydantic import BaseModel

from akasha.domain.execution import Execution
from akasha.domain.trigger import TriggerType


class ExecutionDetail(BaseModel):
    """The canonical execution representation every trigger produces."""

    id: str
    project_id: str
    version: int
    status: str
    trigger_type: TriggerType
    trigger_id: str | None
    correlation_id: str | None
    created_at: datetime

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
        )
