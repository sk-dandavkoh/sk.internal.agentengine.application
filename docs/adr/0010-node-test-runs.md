# ADR 0010: Node test runs

Status: accepted (2026-10-07)

## Context

Building a workflow means trying nodes: does this HTTP request return what
the next node expects, does the prompt produce usable text, is the path
right? Editors like n8n let a user run one step and look at its output
before going on. Here the only way to see a node work was to publish a
version and run the whole workflow, then read the run's report.

Architecture §6.2 says a draft is not executable "unless the execution
system explicitly supports draft execution", and §7 requires that an
execution never runs a later modified draft. Executions are persisted,
evented, retried, recovered and reported; none of that fits a quick probe
of one node with inputs the user typed.

## Decision

1. **A test run is not an execution.** `POST
   /api/v1/workflows/{workflow_id}/nodes/{node_id}/test` runs one node of
   the *saved draft* once with inputs from the request. Nothing is
   persisted: no execution row, no node state, no events, no metrics. The
   response carries the outputs, the error and the captured log lines. One
   structured log line (`node_test_run`: workflow, node, type, status,
   error code, duration; no values) records that it happened.
2. **The same path as a run attempt.** `runtime.node_test.NodeTester`
   calls the runtime's `execute_node`: input validation against the port
   contract, timeout, error classification and redaction, known-secret
   masking in outputs. Config is checked against the node's schema first.
   Credentials are scoped to references in that node's draft config
   (`NodeCredentials`), logs are captured and redacted by
   `NodeLogCapture`, the output size limit applies, and outputs are
   redacted again before they leave, like report outputs.
3. **What the user sees.** `expected_revision` must match the draft (409
   otherwise), so a test never runs a config the user no longer sees.
   Unknown node ids are 404, uninstalled node types and nodes that wait
   for a person (`waits_for_input`: nothing could answer) are
   `422 NODE_NOT_TESTABLE`. A node failure is a result (`status:
   failed`), not an error response.
4. **Editors only.** It is a write under `{workflow_id}`, so it needs edit
   access, like publishing and running. A test can do nothing an editor
   could not do by publishing and running the workflow.
5. **Side effects are real.** There is no dry-run mode: nodes do not have
   one. The editor asks for confirmation before testing a node that
   declares `has_side_effects`.
6. **Bounded.** A test runs at most two minutes (or the configured node
   timeout, if shorter); there is no cancellation endpoint, the timeout
   is the bound.

## Alternatives

- **Draft executions** (run the whole draft as an execution): would need
  draft snapshots, a second kind of execution in every list, report and
  trigger, and rules for which drafts may run. Much larger; a single-node
  probe answers most "does this work" questions.
- **Client-sent config**: the test would run whatever the client sends,
  including credential references the saved draft does not hold. Using
  the saved draft keeps the backend authoritative and the credential
  scope identical to a run.
- **Persisting test runs**: useful history, but it would mix probes into
  execution monitoring, metrics and reports. Revisit if users ask for it.

## Consequences

- The editor can offer "Test node", prefilled with the inputs the node
  received in the workflow's latest run (reports now list them, derived
  from upstream outputs with `Execution.delivers`, the runtime's rule).
- Tests are not visible in monitoring. The `node_test_run` log line is
  the audit trail.
- A test of a long-running node holds an HTTP request for up to two
  minutes.
