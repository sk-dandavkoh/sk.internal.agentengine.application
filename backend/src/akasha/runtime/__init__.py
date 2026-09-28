"""Execution runtime: graph scheduling and node execution.

Must be usable from CLI, workers, tests and application services alike.

Component map (architecture §11):
    GraphLoader / GraphValidator / DependencyResolver -> ``runtime.plan``
    NodeExecutor                                      -> ``runtime.executor``
    ExecutionRuntime / Scheduler / StateManager       -> ``runtime.runtime``

Dependency rules:
    - may depend on: domain, application interfaces
    - must not depend on: fastapi / HTTP
"""
