"""Execution use cases (roadmap Phase 08): create and inspect executions.

Creating an execution pins one immutable ProjectVersion and records it as
QUEUED — accepted for execution. *Running* it is the runtime's job
(``akasha.runtime``); until the worker/queue architecture exists (Phase 22)
the caller hands the execution id to ``ExecutionRuntime.run``. Triggers
(Phase 10) will converge on ``create_execution``.
"""

import logging
from collections.abc import Mapping
from typing import Any

from akasha.application.persistence import UnitOfWork
from akasha.application.projects import ProjectVersionNotFoundError, UowFactory
from akasha.domain.execution import Execution, ExecutionMetadata, TimeoutPolicy
from akasha.errors import AkashaError

logger = logging.getLogger("akasha.application.executions")


class ExecutionNotFoundError(AkashaError):
    code = "EXECUTION_NOT_FOUND"
    http_status = 404


async def load_execution(uow: UnitOfWork, execution_id: str) -> Execution:
    execution = await uow.executions.get(execution_id)
    if execution is None:
        raise ExecutionNotFoundError(
            "Execution not found.", details={"execution_id": execution_id}
        )
    return execution


class ExecutionService:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    async def create_execution(
        self,
        project_id: str,
        version: int,
        *,
        input: Mapping[str, Any] | None = None,
        metadata: ExecutionMetadata | None = None,
        timeout_policy: TimeoutPolicy | None = None,
    ) -> Execution:
        """Pin ``version`` and queue a new execution of it with ``input``
        (a JSON object; entry nodes such as ``core.input`` read it).

        Never reads the draft: later draft edits or publishes cannot change
        what this execution runs. Retry policy stays the default (no retry)
        until retry enforcement exists (Phase 23).
        """
        async with self._uow() as uow:
            snapshot = await uow.versions.get(project_id, version)
            if snapshot is None:
                raise ProjectVersionNotFoundError(
                    "Project version not found.",
                    details={"project_id": project_id, "version": version},
                )
            execution = Execution.create(
                project_id,
                snapshot,
                metadata=metadata,
                timeout_policy=timeout_policy,
                input=input,
            )
            execution.enqueue()
            await uow.executions.save(execution)
            await uow.commit()
        logger.info(
            "execution_created",
            extra={
                "event": "execution_created",
                "execution_id": execution.id,
                "project_id": project_id,
                "project_version": version,
                "correlation_id": execution.metadata.correlation_id,
            },
        )
        return execution

    async def get_execution(self, execution_id: str) -> Execution:
        async with self._uow() as uow:
            return await load_execution(uow, execution_id)
