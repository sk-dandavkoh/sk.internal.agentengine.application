"""Execution runtime: graph scheduling and node execution.

ExecutionRuntime, Scheduler, DependencyResolver, NodeExecutor, StateManager
(Phase 07-08). Must be usable from CLI, workers, tests and application
services alike.

Dependency rules:
    - may depend on: domain, application interfaces
    - must not depend on: fastapi / HTTP
"""
