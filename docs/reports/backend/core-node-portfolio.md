# Core Node Portfolio

Status: implemented (2026-10-06). Scope: core nodes only — no vendor
integrations, no plugin nodes, no general shell. Decisions that change the
architecture are in ADR 0009 (waiting nodes) and `docs/architecture.md`
(§3.6, §9, §14, §37, §63, §64, §75, §136, §137).

Guiding rule: **maximum capability, minimum redundancy**. The measure of
success is not the number of nodes. This change adds **four** node types
and extends **one**, and rejects every other candidate on the checklist.

---

## 1. Starting point

The engine (Phases 00–27 plus ADR 0005–0008) had 19 built-in nodes, a
generic runtime (control flow by an `is_control_flow` capability, no
special-cased node types), a tool loop whose tools *are* registry nodes
(ADR 0005), declarative data shaping without an expression engine, an SSRF
guard with DNS pinning, a per-workflow workspace guard, and a `WAITING`
execution status that nothing used.

Gaps found by the inventory below: no way to involve a person, no database
access, no list processing (filter/map/sort/group) or CSV/JSON parsing, no
rendered-page/browser automation, and HTTP requests whose URL could not be
parameterised by data.

## 2. Existing nodes examined

Every built-in was read in full (definition, config schema, runtime,
tests), together with the node contract (`domain/node.py`), the registry,
the runtime/executor/plan, the agent tool loop, the network and path
guards, persistence, the API and the schema-driven frontend.

| Node | Capability | Inputs | Outputs | Side effects | Agent tool | Relevant overlaps |
|---|---|---|---|---|---|---|
| core.input | entry: execution payload (or one field) | — | value | none | (no inputs) | — |
| core.output | marks a result | value | value | none | yes, pointless | — |
| core.log | structured log line, pass-through | value | value | log | yes | — |
| core.if | route by condition (8 operators, dotted `field`) | value | true/false | none | no (control flow) | operators reused by transform `filter` |
| core.switch | route by equality, 5 cases | value | case_0..4, default | none | no (control flow) | — |
| core.delay | in-process wait ≤ 1 h | value? | value | none | yes | durable delays: see P2 |
| core.merge | join live branches into a list | items* | items | none | yes | — |
| core.set | object from constants + input under key | value? | value | none | yes | — |
| core.pick | value at a dotted path | value | value | none | yes | = transform step `pick` (kept, see §6) |
| core.http | one HTTP request (6 methods, credential headers, redirects, size cap) | body?, **query?, variables?** (new) | status, headers, body | network | yes | web_fetch (GET + text extraction) |
| core.web_fetch | page → readable text (static HTML) | url | url, status, title, text, truncated | network | yes | browser (renders JS) |
| core.web_search | SearXNG search | query | results | network | yes | — |
| core.smtp | send plain-text mail | subject, body | message_id, rejected_recipients | external mail | yes | the notification capability |
| core.llm | one completion, structured output, returns tool calls | variables | text, structured, tool_calls, metadata | provider call | yes | agent |
| core.agent | model + registry tools loop | variables | text, structured, steps, metadata | via tools | no (no nesting) | — |
| core.file_read | UTF-8 file from workspace | path | content, size | none | yes | — |
| core.file_write | UTF-8 file to workspace (overwrite/append/create) | path, content | path, size | filesystem | yes | — |
| core.file_list | directory listing | path? | entries | none | yes | exists/metadata via listing |
| core.command | operator-allowlisted program, argv only | args?, stdin? | exit_code, stdout, stderr, truncated | process | yes | not a shell (see §24) |

New/extended in this change: **core.human, core.transform, core.database,
core.browser** (new), **core.http 1.1.0** (extended).

## 3. Capability inventory

| Area | Capability | Before | After |
|---|---|---|---|
| AI | completion, structured output | core.llm | unchanged |
| AI | tool calling / agents | core.agent (+ registry) | unchanged; new nodes are tools |
| AI | prompt templates | core.llm `{{ path }}` | + transform `template` (same renderer) |
| AI | embeddings / retrieval / vector store | — | not core (P3, §25) |
| Human | approval, review, form input, timeout, resume | — | **core.human** + persistent WAITING |
| Browser | rendered pages, forms, extraction, screenshots | (static text: web_fetch) | **core.browser** |
| Data | parse/serialize JSON, CSV | — (HTTP decodes JSON by content type) | **core.transform** |
| Data | filter, map, sort, limit, unique, flatten, group, aggregate | — | **core.transform** |
| Data | pick by path, constants | core.pick, core.set | unchanged |
| Data | XML, regex, date/time, type conversion | — | P2 (§25) |
| Database | SQLite, PostgreSQL, parameterised statements | — | **core.database** |
| Files | read/write/list text | core.file_* | unchanged |
| Files | delete/move/copy, binary, archives | — | P2/P3 (§25) |
| HTTP | methods, headers, credential headers, size cap, redirects | core.http | unchanged |
| HTTP | query parameters, URL path from data | — (URL fixed in config) | **core.http 1.1.0** |
| Control flow | if, switch, merge, parallel (graph), delay, retry/timeout (runtime) | yes | unchanged |
| Control flow | wait for a person | — | core.human |
| Control flow | loop/for-each, try/catch, subworkflow | — | P1/P2, runtime work (§17, §25) |
| Triggers | manual, API, cron, interval, webhook, internal event | yes | unchanged |
| Events | internal event trigger | yes | unchanged; emit/wait-for-event P2 |
| Notifications | e-mail | core.smtp | unchanged (no vendor messaging) |

## 4. Redundancy analysis

For every proposed capability the ten mandated questions were answered;
the four that became nodes are summarised here (the rest are §5).

**core.human — wait for a person.**
(1) Suspend a run until someone approves/rejects/fills in data, or a
deadline passes. (2) Related: core.delay, core.if. (3) No node could
suspend at all. (4) Not by composition: nothing persisted a waiting state.
(5) core.delay could not be extended: it waits for *time* inside one
attempt; waiting for a *person* needs a different runtime outcome.
(6) No overlap: it is the only node that waits for outside input.
(7) Approval, review and form input are one capability (a schema-shaped
answer); one node covers all three. (8) Yes — a new runtime outcome
(`NodeWait`). (9) Core: the most requested workflow pattern. (10) Needs the
runtime and persistence; a plugin cannot add a node status.

**core.transform — reshape data.**
(1) Parse/serialize JSON and CSV; filter/map/sort/group/aggregate lists;
format text. (2) core.set, core.pick, core.if. (3) set/pick handle single
values only; nothing iterated a list or parsed text. (4) Not by
composition: lists need a loop, which the runtime does not have.
(5) Extending core.pick into a pipeline would change its single-purpose
contract (and its port semantics); a new node with steps is clearer.
(6) Overlap with core.pick is one step (`pick`), accepted for
compatibility (§6). (7) One node with ordered declarative steps replaces
~14 candidate nodes (Map, Filter, Sort, JSON Parse, CSV Parse, …).
(8) Yes — pure data plumbing, but no existing node can express it.
(9) Every integration workflow needs it. (10) Generic, no vendor.

**core.database — run SQL.**
(1) One parameterised statement on SQLite or PostgreSQL. (2) none.
(3)/(4) No. core.command could run `sqlite3` only with an operator-allowed
binary and string-built SQL — exactly the injection risk this avoids.
(5) Nothing to extend. (6) No overlap. (7) Select/insert/update/delete are
SQL, not node types: one node. (8) Yes. (9) Core (generic DB access).
(10) Generic engines, no vendor API.

**core.browser — automate a rendered page.**
(1) Open a URL in headless Chromium, run steps (click, fill, select, press,
wait_for, extract, screenshot), return text and extractions.
(2) core.web_fetch, core.http. (3) web_fetch only parses static HTML:
no JavaScript, no forms. (4) Not by composition. (5) Extending web_fetch
would turn a cheap, dependency-free text fetch into a 150 MB optional
browser — the two have different cost, security and dependency profiles.
(6) Overlap with web_fetch for static pages, accepted: web_fetch stays the
cheap default, browser the heavy tool for pages that need it. (7) One node
with steps instead of Navigate/Click/Fill/Extract/Screenshot nodes: a
browser session cannot cross node boundaries safely (§12). (8) Yes.
(9) Explicitly in scope; common for portals without APIs. (10) Generic
web, not a vendor integration.

**core.http extension (1.1.0).** Query parameters and URL path variables
are part of "an HTTP request" — extending the existing node, not adding
HTTP GET/POST/REST/API nodes. Backward compatible (optional inputs).

### Redundancy table

| Capability | Existing Node(s) | Extension Possible? | Composition Possible? | New Node? | Decision / Reason |
|------------|------------------|---------------------|-----------------------|-----------|-------------------|
| Human approval / review / form input | — (core.delay waits for time only) | No: needs a new runtime outcome | No | **Yes: core.human** | One node for approval, review and input; persistent WAITING (ADR 0009) |
| Escalation on no answer | core.human, core.smtp | — | Yes: `timed_out` → smtp / human | No | Composition |
| Assignment to named users | core.human | Later (config) | — | No | P2: needs a user/role notion per wait |
| List filter / map / sort / group / aggregate | core.set, core.pick | No: single-value nodes | No (no loops) | **Yes: core.transform** | One step pipeline instead of ~10 nodes |
| JSON / CSV parse & serialize | core.http (JSON by content type) | Partly (http only) | No for files/LLM text | In core.transform | Steps, not nodes |
| Text template | core.llm templates | — | core.transform `template` | No separate node | Same renderer reused |
| Pick by path | core.pick | — | — | No | Kept; = transform `pick` (compatibility) |
| XML / regex / date / type conversion | — | Yes: transform steps | — | No | P2 |
| SQL (select/insert/update/delete) | — (core.command could run a CLI, unsafely) | No | No | **Yes: core.database** | One statement node; params only; SQLite + PostgreSQL |
| SQL Select / Insert / Update / Delete nodes | core.database | — | — | No | Statement kind is SQL, not a node type |
| Multi-statement transaction | core.database | Yes (later: statement list) | — | No | P2 |
| Rendered web pages, forms, extraction | core.web_fetch (static text) | No: different cost/dependency/security profile | No | **Yes: core.browser** | One node with steps; isolated per attempt |
| Browser Navigate/Click/Fill/Extract/Screenshot | core.browser | — | — | No | Steps; sessions cannot cross nodes safely |
| Web scraper | core.browser, core.web_fetch | — | Yes: browser → transform → database | No | Composition |
| HTTP GET/POST/PUT/PATCH/DELETE, REST/API request | core.http | — | — | No | Methods are config |
| HTTP query & path parameters from data | core.http | **Yes (1.1.0)** | — | No | Extended: `query`, `variables` inputs |
| Read Text / JSON / CSV file | core.file_read | — | Yes: file_read → transform | No | Composition |
| File exists / metadata | core.file_list | — | Yes | No | Listing shows it |
| File delete / move / copy | — | Yes (file nodes) | — | No | P2 (destructive; not needed now) |
| Archive / extract | — | — | — | No | P3: zip-slip/bomb risk, no artifact storage |
| For each / loop | — | Runtime change | No | No | P1: subgraph semantics in the runtime |
| Try / catch | core.http `fail_on_error_status` | Runtime change | Partly | No | P2 |
| Retry / timeout | runtime policies | — | — | No | Already runtime semantics |
| Parallel / merge | graph, core.merge | — | — | No | Exists |
| Delay (long, durable) | core.delay (≤ 1 h) | Yes: reuse ADR 0009 | — | No | P2 |
| Subworkflow / call workflow | — | Runtime (child executions) | No | No | P1, reuse ADR 0009 |
| Stop / return | core.output | — | — | No | P3 |
| Rate limit | — | — | — | No | P3 |
| Emit / wait for internal event | EVENT trigger | Wait: reuse ADR 0009 | Emit: execution events | No | P2 |
| Cron / interval / webhook / API / manual / one-time | triggers | — | cron ≈ one-time | No | Exists |
| Notification (e-mail) | core.smtp | — | — | No | Exists; no vendor messaging |
| LLM / agent / structured output / tool calling | core.llm, core.agent | — | — | No | Exists; provider abstraction |
| ChatGPT / Claude / OpenAI nodes | core.llm (`provider`) | — | — | No | Provider is config |
| Embeddings / retrieval / evaluation | core.llm | — | Evaluation: llm + if | No | Retrieval needs vector storage: P3 |
| Shell / execute command | core.command (allowlisted, argv) | — | — | No | No general shell (§24) |

## 5. Rejected nodes

| Candidate | Why not |
|---|---|
| HTTP GET/POST/PUT/REST/API Request | core.http covers every method; 1.1.0 adds query/path data |
| Webhook Response node | webhook trigger returns the queued execution; synchronous responses would need a different trigger model (P3) |
| ChatGPT/OpenAI/Claude/Generic AI | the provider abstraction (core.llm/core.agent `provider`) covers them |
| Structured Output node | core.llm/agent `output_schema` |
| Prompt/Template node | core.llm templates; transform `template` |
| Evaluation node | core.llm with an evaluation prompt + core.if |
| Embeddings / Retrieval / Vector Search | need vector storage (architecture §159 future); P3 |
| Approve / Reject / Review / Form / Assign / Escalate nodes | one core.human; escalation = `timed_out` → smtp/human |
| Browser Navigate/Click/Fill/Extract/Screenshot/Close | steps of core.browser; sessions cannot cross nodes safely |
| Web Scraper / Website Automation | browser → transform → database |
| Map / Filter / Reduce / Sort / Group / Aggregate / Split / Flatten / Deduplicate | core.transform steps (Split = `limit`/`pick`; Reduce = `aggregate`) |
| JSON Parse / JSON Serialize / CSV Parse / CSV Serialize | core.transform steps |
| XML Parse/Serialize | P2: needs a hardened parser (defusedxml) — no real workflow in scope yet |
| String / Regex / Date / Type conversion nodes | P2 as transform steps; template covers formatting |
| Join (of two lists) | merge + transform covers most; keyed joins P2 |
| SQL Select/Insert/Update/Delete, SQL Query | core.database (one statement node) |
| Read Text / Read JSON / Read CSV | core.file_read + transform `parse_*` |
| File Exists / Metadata | core.file_list |
| File Delete / Move / Copy | P2 (destructive file ops; not needed by the target workflows) |
| Archive / Extract | P3: zip-slip/zip-bomb surface without binary/artifact storage |
| For Each / Loop / While | runtime subgraph semantics needed (P1, §17) |
| Try/Catch | runtime error-routing semantics needed (P2); HTTP `fail_on_error_status` covers the common case |
| Retry / Timeout nodes | runtime policies (per execution) |
| Parallel node | the graph is parallel by construction |
| Stop / Return | core.output marks results; failing on purpose: P3 |
| Rate Limit | P3 |
| Subworkflow / Call Workflow | P1, needs a child-execution wait (can reuse ADR 0009) |
| One-time trigger | cron covers it; P3 |
| Emit Event / Wait for Event | event trigger exists; wait-for-event can reuse ADR 0009 (P2) |
| Slack/Teams/Jira/SAP/… | out of scope (vendor) |
| Shell / Execute Command | refused; see §24 |

## 6. Extended nodes

- **core.http 1.1.0**: optional `query` (object; lists repeat a parameter;
  merged into — never replacing — the URL's own query) and `variables`
  (object) inputs. `{{ name }}` placeholders are allowed only after the
  host and filled URL-encoded, so data (or a model) chooses a resource,
  never a server (`HTTP_INVALID_URL`, `HTTP_URL_VARIABLE_MISSING`). A
  regression found by the tests — an empty `params={}` made httpx drop the
  URL's query — is fixed by merging explicitly.
- **core.pick** is unchanged and not deprecated: it is the one-step form of
  a transform `pick` step, and persisted workflows use it.
- **core.agent** now refuses waiting nodes as tools (`waits_for_input`).
- **core.if** operators became public (`OPERATORS`) so transform `filter`
  uses exactly the same semantics.

## 7. New nodes

| Node | Category | Inputs | Outputs | Capabilities | Agent use |
|---|---|---|---|---|---|
| core.human | human_interaction | value? | approved, rejected, timed_out, response | control flow, waits_for_input | HUMAN only |
| core.transform | transform | value | value | — | BOTH (model supplies `value`) |
| core.database | database | params? | rows, row_count, truncated | side effects, cancellation | BOTH (model supplies `params` only) |
| core.browser | web | url, values? | url, title, text, extracted, truncated | side effects, cancellation | BOTH (model supplies `url`, `values`) |

## 8. Justification of each new node

See §4. In one line each: core.human is the only way to suspend for a
person (new runtime outcome); core.transform is the only way to process
lists and parse text without an expression engine or loops; core.database
is the only safe way to reach SQL; core.browser is the only way to read
JavaScript-rendered pages and drive forms.

## 9. Priorities

| Priority | Capability | State |
|---|---|---|
| P0 | Human-in-the-loop with persistent WAITING/resume | **implemented** |
| P0 | Database access (SQLite + PostgreSQL, parameterised) | **implemented** |
| P0 | List processing and JSON/CSV parsing | **implemented** |
| P1 | Browser automation (Playwright) | **implemented** |
| P1 | HTTP query/path parameters | **implemented** |
| P1 | For-each over a list with a subgraph | open (runtime) |
| P1 | Call workflow / subworkflow | open (runtime; reuse ADR 0009 waiting) |
| P2 | Try/catch error routing | open |
| P2 | Durable long delay, wait-for-internal-event | open (reuse ADR 0009) |
| P2 | File delete/move/copy, binary files (needs artifact storage) | open |
| P2 | Transform steps: regex, dates, type conversion, keyed join, XML | open |
| P2 | Multi-statement database transactions | open |
| P2 | Human task assignment to named users, inbox across executions | open |
| P2 | Richer side-effect taxonomy (read-only vs destructive) | open |
| P3 | Archives, vector retrieval, rate limiting, webhook responses, one-time trigger, multi-node browser sessions | not core / later |

## 10. Agent integration

- No parallel tool system: the agent still resolves tools from the live
  registry; the new nodes are tools because they are registry nodes.
- What a model may supply is exactly a node's input ports
  (`tool_parameters`): database `params` (never SQL), http `body`/`query`/
  `variables` (never the host), transform `value`, browser `url`/`values`
  (never selectors). The author fixes everything else in config.
- Waiting nodes cannot be tools (`AGENT_TOOL_INVALID` before the first
  model call); a `NodeWait` returned anyway becomes a tool error.
- Tool results stay masked for resolved secrets and cut at
  `max_tool_result_chars`; database errors return codes and SQLSTATE only.
- Side effects: the existing `has_side_effects` / `supports_cancellation`
  flags are set truthfully for the new nodes (contract test); the new
  `waits_for_input` flag marks HUMAN-only nodes. A finer taxonomy was not
  invented (P2).

## 11. Human-in-the-loop

Mechanism (ADR 0009): `NodeWait` → node `WAITING` (request persisted) →
execution `WAITING` when nothing else can run (worker released, never
stale) → `POST /executions/{id}/inputs/{node_id}` validates the answer
against the persisted schema and queues the execution (`wake`) → a worker
resumes it, the node's next attempt gets `context.resume` → routes.

- Persistence: `node_executions.wait` (prompt, schema, deadline),
  `node_executions.resume` (answer, responder, timed_out),
  `executions.wake_at` (migration 0018).
- Timeout: `timeout_seconds` per node → `timed_out` branch; the execution
  deadline still applies (counts waiting time) → `TIMEOUT`. Settled by
  every worker's recovery pass (`expire_waits`, `FOR UPDATE SKIP LOCKED`).
- Cancellation: `POST /cancel` on a waiting execution cancels it directly
  (no owner to signal); its waiting node ends `CANCELLED`.
- Restart: nothing in memory; proven by a test that discards the runtime
  and its database engine.
- Multiple workers: answers and expiry are CAS writes / row claims; the
  resumed run is an ordinary queued job.
- Security: answering requires workflow EDIT access (viewers 403);
  forged/invalid answers 422; stale/duplicate answers 409; events never
  carry the answer.
- Frontend: "Waiting for input" card on the execution page with a form
  generated from the response schema; status labels and live events.
- A real bug found while testing: two concurrent answers deadlocked
  PostgreSQL (share lock from the read, then two updates → 500). Fixed by
  reading and writing in separate transactions (CAS decides), like
  `runtime.cancel`.

## 12. Browser / Playwright

- One node, steps in order, one browser process + context per attempt,
  closed in `finally` (timeout, cancellation, failure included). No
  session crosses attempts, executions or workflows (tested with cookies).
- Network isolation: `context.route("**/*")` performs every request
  through core.http's `fetch` (guard + DNS pinning per request and
  redirect hop, response cap, timeout, ≤ 300 requests); host resolution is
  disabled (`--host-resolver-rules`) and unrouted traffic hits a dead
  proxy, so WebSockets and anything not interceptable fail closed.
  Chromium does not re-route a fulfilled redirect, so the engine follows
  redirects itself (each hop guarded); known ceiling: the page keeps its
  first URL.
- Downloads off, service workers blocked, no upload step, dialogs
  dismissed; screenshots are viewport-only PNGs into the workspace, capped
  by the workspace file limit.
- Optional dependency: `playwright~=1.63.0` as the `browser` extra (same
  release as the frontend's Playwright); missing → `BROWSER_NOT_CONFIGURED`.
- Not OS automation: no file system, shell or desktop access.

## 13. Database / SQLite

- SQL text is config (author, publish time); data only via bound `params`
  (list → positional, object → named). One statement per attempt: SQLite
  refuses `a; b`; for PostgreSQL every statement is prepared (extended
  protocol) — the tests caught that psycopg otherwise runs parameterless
  SQL through the simple protocol, where `SELECT 1; DROP TABLE x` ran both.
- One connection per attempt, one transaction, commit on success, rollback
  on error; `read_only` → SQLite `mode=ro` / PostgreSQL read-only
  transaction.
- SQLite files live in the workflow workspace (path guard); an authorizer
  denies `ATTACH`/`DETACH` (and with it `VACUUM INTO`), extension loading
  stays off. `:memory:` is not offered: a database living for one
  statement holds nothing.
- PostgreSQL: connection URL is a credential; host guarded and pinned via
  `hostaddr`; Unix sockets/several hosts refused; errors report type and
  SQLSTATE only (no URL, no row data). SQLSTATE class 40 is TRANSIENT.
- Timeouts interrupt (SQLite `interrupt()`) / cancel (PostgreSQL); results
  capped at `max_rows` with `truncated`. Decimal → exact text, dates → ISO,
  bytes → base64.

## 14. Files

Unchanged nodes; reading structured files is composition
(`file_read → transform parse_csv/parse_json`), writing is
`transform to_csv/to_json → file_write`. Delete/move/copy, binary files and
archives were deliberately not added (§5, §25). The workspace guard now
also places SQLite files and screenshots.

## 15. HTTP

core.http 1.1.0 (§6). All existing protections apply unchanged to the new
inputs (guard per hop, credential headers dropped on cross-origin
redirects, size and time caps). core.browser reuses the same `fetch`.

## 16. Transformations

core.transform steps: `parse_json`, `to_json`, `parse_csv` (header row,
`,`/`;`/tab/`|`), `to_csv`, `pick`, `filter` (core.if operators on `field`
of each item), `map` (`{out: path}`), `sort` (`field`, `descending`; None
first), `limit`, `unique` (by `field`), `flatten` (one level), `group`
(by `field` → object of lists), `aggregate` (`count`/`sum`/`min`/`max`/
`avg`), `template`. Bad data → VALIDATION error with a stable code; a
step missing its parameter → CONFIGURATION error. No expressions, no
attribute access: paths only index mappings and lists (tested with dunder
keys).

## 17. Control flow

Evaluated: if/switch/merge/delay exist; parallelism is the graph; retry
and timeout are runtime policies. New: core.human routing. Not built:
for-each/loop (needs subgraph execution and cycle-free iteration
semantics in the runtime — a separate, architecturally significant phase),
try/catch (error routing in the runtime), subworkflow (child executions;
ADR 0009's waiting is the intended building block).

## 18. Security

Tested (see §20): SSRF (metadata IP, loopback by name, private ranges,
redirect hops) for browser and PostgreSQL; URL variable host injection;
path traversal and symlink escape (SQLite, screenshots); ATTACH /
VACUUM INTO; SQL injection via params (SQLite, PostgreSQL, agent tool);
stacked statements; credential URLs never echoed; unauthorized answers
(viewer 403), invalid (422) and duplicate (409) answers; browser session
isolation; malicious URLs (`file:`, `javascript:`); expressions with code
or attribute access rejected at publish; secrets absent from logs, events
and exports in the E2E runs.

## 19. Resource limits

New limits (architecture §75 table): database statement duration and
rows; browser attempt duration and request count; human answer size and
prompt length. All new nodes run under the existing per-node timeout,
output caps (node/execution) and concurrency limits; the HTTP response cap
applies to every browser request.

## 20. Tests

All tests use real infrastructure where behaviour depends on it; no
sleeps for synchronisation (one bounded `asyncio.sleep(0.2)` lets a SQLite
statement start before a cancellation test cancels it).

- Domain: state matrices for the new node status and `wake` action
  (legal and illegal), stateful fuzzer extended with wait/answer/expire
  (invariants: terminal ⇒ only terminal nodes, waiting ⇒ request present,
  rehydration), 15 focused waiting tests.
- Nodes: core.human (14), core.transform (32), core.database (25, real
  SQLite + PostgreSQL), core.browser (17, real Chromium), core.http
  query/variables (+9), agent tools (+3), contract suite over all 23
  built-ins.
- Integration (PostgreSQL): human-in-the-loop lifecycle (7 incl. the
  restart proof, deadlines, cancellation, concurrent answers, parallel
  branches), migration 0018 on existing data + guarded downgrade, worker
  recovery pass expiring a wait (Redis).
- E2E (production composition root): workflows A–D (§21).
- Frontend: unit/contract (PendingInputs incl. the unmount race, live
  labels, OpenAPI contract for the new endpoints) and a fullstack
  Playwright journey (§22).

Results of the final runs are in §27a.

## 21. E2E

`backend/tests/integration/test_core_portfolio_e2e.py` — API → PostgreSQL
→ Redis queue → worker → runtime → real nodes; loopback LLM/HTTP stand-ins,
Mailpit, real SQLite, real Chromium:

- **A** HTTP → Transform (parse, filter, serialize) → Database (insert via
  `json_each`) → Agent (database tool) → Human (WAITING, answered over
  HTTP) → Transform/Set → HTTP POST.
- **B** Webhook (HMAC) → Transform (validate/normalize) → Agent → Human
  (form field `priority`; viewer 403, invalid 422, duplicate 409) →
  Database → SMTP (Mailpit).
- **C** Agent (structured URL) → Browser (extract) → Transform → Database.
- **D** File Read (CSV in workspace) → Transform (parse_csv, filter) →
  Database → Agent (database tool); export contains no secret.

## 22. Frontend

- Node library, search, categories and inspector are schema-driven: the
  new nodes appear and are configured without node-specific UI (glyphs
  added for `human_interaction`, `database`, `file`).
- New: "Waiting for input" card on the execution page; the form is
  generated by the existing `PropertyFields` from the response schema;
  server decides (422/409 shown), no optimistic update. A bug found by
  the fullstack test — a run that finished before the answer's response
  unmounted the form and swallowed the confirmation — is fixed by owning
  the mutation in the parent (unit test reproduces the race).
- Report/node status `waiting`, live labels for the new events.
- `frontend/tests/fullstack/human.spec.ts`: library shows the new nodes →
  place, configure (backend validation), connect Human Input → publish →
  run → WAITING card with prompt → answer in the UI → completed on the
  approved branch.

## 23. Architecture decisions

1. Waiting is a runtime outcome (`NodeWait`) with an owner-less WAITING
   execution; resume is a queue hand-off (ADR 0009).
2. Answering continues the attempt (not a retry).
3. One node per capability; operations are config (methods, SQL, steps).
4. Data via input ports, destinations/SQL/selectors via config — the rule
   that makes every node agent-safe.
5. Browser traffic is performed by the engine's HTTP client.
6. Optional heavy dependencies are extras (`browser`), not image defaults.

## 24. Deliberate non-implementations

- No shell/execute-command node was added. The pre-existing
  `core.command` (ADR 0005) is unchanged: off unless the operator lists
  executables, argv only, minimal environment — not a general shell. No
  new node gives hidden shell or host access (SQLite cannot attach files,
  the browser has no file system or network of its own).
- No vendor integrations, no plugin nodes, no new plugin capabilities.
- No expression engine (transform stays declarative).
- No new permission/side-effect architecture next to `NodeCapabilities`.

## 25. Remaining gaps

For-each/loop and subworkflows (P1); try/catch, durable long delays,
wait-for-event, file delete/move/copy and binary files, more transform
steps, multi-statement transactions, task assignment/inbox (P2);
archives, vector retrieval, rate limiting, webhook responses, browser
sessions across nodes (P3).

## 26. Future candidates

- `core.call_workflow` and `for_each` on top of ADR 0009 (child execution
  completion wakes the parent).
- `core.delay` durable mode (a timer-only wait).
- Transform steps `regex`, `date`, `cast`, `join`; `parse_xml` with a
  hardened parser.
- A human-task inbox (read model over waiting node executions).

## 27. Known limitations

- An answer sent while another branch of the same execution runs is
  refused (409) and must be resent once the execution waits.
- Wait deadlines are honoured within the recovery interval
  (`AKASHA_QUEUE_RECOVERY_SECONDS`, default 30 s).
- The execution timeout counts waiting time (default 1 h): long approvals
  need a higher `AKASHA_LIMIT_EXECUTION_TIMEOUT_SECONDS`.
- Transform step parameters are checked at run time, not publish time
  (no `oneOf` in the config-schema subset).
- core.browser: one browser launch per attempt (~0.5 s); after an
  engine-followed redirect the page keeps its first URL; multiple
  `Set-Cookie` headers in one response may be merged by the HTTP client.
- PostgreSQL statements are prepared: `%` in SQL must be written `%%`
  (psycopg placeholder syntax); placeholders are `%s` / `%(name)s`.
- Paths (pick, field, map) must start with a name: a top-level list is
  indexed after a step such as `group`/`limit`, or by a template
  `{{ value.0 }}`.

## 27a. Test results of the final runs

All commands were executed on 2026-10-06 in this checkout.

| Check | Command | Result |
|---|---|---|
| Backend tests (all but the Docker Compose deployment test) | `uv run pytest --ignore=tests/deployment` | **2450 passed**, 5 skipped (external OpenRouter smoke tests: no API key), 0 failed |
| Backend lint | `uv run ruff check .` | PASS |
| Backend format | `uv run ruff format --check .` | PASS |
| Backend types | `uv run mypy` (configured scope: `src/akasha`) | PASS |
| Lockfile | `uv lock --check` | PASS |
| Frontend | `npm run check` (format, lint, typecheck, vitest, build) | PASS — 41 files, 530 tests, build OK |
| Frontend fullstack E2E (real backend) | `npx playwright test -c playwright.fullstack.config.ts` | 28 passed, 1 failed: `workflows.spec.ts › triggers are configured…` — fails on an untouched `HEAD` checkout too and passes/fails alternately on this branch (pre-existing flake). The new `human.spec.ts` passes. |
| Frontend mocked E2E | `npx playwright test` | 35 failed — identical 35 failures on an untouched `HEAD` checkout ("Could not check your session": the mocked session no longer matches the session schema). Pre-existing, not caused by this change. |

Not run: `tests/deployment` (builds the Docker images and runs Compose);
`mypy .` over the test tree reports 17 pre-existing errors in test files
untouched by this change (the baseline had 19; the two this change
introduced in test helpers were fixed).
