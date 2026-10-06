# ADR 0009: Waiting nodes (human-in-the-loop)

Status: accepted (2026-10-06)

## Context

Workflows need a person in the loop: approve a draft before it is sent,
fill in data the workflow cannot know, review an agent's result. The
domain had a `WAITING` execution status since Phase 07 and architecture §9
says a node may return "a waiting state", but no runtime path used either;
architecture §137 left human interaction as a future extension.

An approval can take hours or days. The engine runs executions in workers
that heart-beat their ownership; a run that stops heart-beating is
recovered as stale (Phase 23). A node that simply blocks (sleep, poll)
would hold a worker slot, die with the process and be "recovered" into a
repeat of its side effects.

## Decision

1. **Waiting is a runtime outcome, not a node loop.** A node attempt may
   return `NodeWait(prompt, response_schema, timeout)` instead of a
   `NodeResult`. Only nodes declaring `NodeCapabilities.waits_for_input`
   may (else `NODE_OUTPUT_INVALID`). The runtime records it with
   `Execution.wait_node`: the node goes `RUNNING → WAITING` (a new node
   status) with its request persisted on the node execution.
2. **A waiting execution has no owner.** When nothing runs or can run and
   a node waits, the scheduler saves the execution `WAITING` and returns.
   No task, heartbeat or worker slot is held; stale-execution recovery
   ignores `WAITING` rows. Restarting every process changes nothing.
3. **Resuming is a queue hand-off.** `resume_node` (an answer) or
   `expire_waits` (a deadline) makes the node `READY` again
   (`WAITING → READY`) and the execution `QUEUED` (new action `wake`). A
   worker resumes it exactly like a recovered run: finished nodes never
   rerun, the answered node's next attempt receives `context.resume`.
   Answering continues the attempt that asked; it is not counted as a
   retry and does not use the retry budget.
4. **Answers are validated where they arrive.** The request carries a JSON
   Schema in the config-schema subset; the domain validates answers with
   the same validator (`422` on mismatch) and the frontend renders the form
   with the same field generator as node settings.
5. **Concurrency by compare-and-swap.** Only a `WAITING` execution can be
   answered, so no scheduler loop holds it. The answer is read and written
   in separate transactions and the write is a CAS on the revision: of two
   simultaneous answers exactly one wins, the other gets `409`.
6. **Deadlines without timers.** `executions.wake_at` (earliest wait
   deadline or the execution deadline, set only while `WAITING`, partial
   index) is scanned by every worker's recovery pass; rows are claimed with
   `FOR UPDATE SKIP LOCKED`. The execution deadline keeps counting waiting
   time, as before (no immortal runs).
7. **Authorization is the workflow's.** Answering is a write: workflow
   editors (owner, editors, admins). Viewers can see what is asked.
8. **One node uses it in core: `core.human`** — approval, review and form
   input in one node, routing `approved` / `rejected` / `timed_out`.

## Alternatives

- **A blocking node (sleep/poll until answered).** Holds a worker slot per
  waiting approval, dies with its process, and stale recovery would repeat
  it. Rejected.
- **Completing the node directly from the API with the answer as output.**
  Avoids a second attempt, but the API would have to know each waiting
  node's output shape (coupling the HTTP layer to node types) and the
  node could not react to the answer (route, validate, time out). Rejected.
- **Counting the answered run as a new attempt.** Reports would show a
  retry that never happened and the retry policy would be consumed by
  waiting. Rejected.
- **A separate "tasks" table and service.** A second source of truth next
  to the execution aggregate. The request and answer belong to the node
  execution they suspend. Rejected for now; a cross-execution task inbox
  can be a read model over node executions later.

## Consequences

- Migration 0018 adds the `waiting` node status, `node_executions.wait` /
  `resume` and `executions.wake_at`. Downgrade refuses while a node waits.
- New events: `node.waiting`, `execution.waiting`, `node.resumed`
  (payload `outcome` only — never the answer). Event consumers ignore
  unknown types, as before.
- SDK 1.2.0 (additive): `NodeWait`, `NodeCapabilities.waits_for_input`,
  `NodeExecutionContext.resume`.
- Waiting nodes cannot be agent tools: a tool call cannot suspend the
  agent's attempt.
- An answer sent while another branch of the same execution is running is
  refused (`409`) and must be sent again once the execution waits. Known
  ceiling; the alternative (queueing answers) needs a second write path
  into a running aggregate.
- Durable long delays (`core.delay` beyond its one-hour in-process cap) and
  "wait for internal event" can reuse this mechanism later; not done here.
