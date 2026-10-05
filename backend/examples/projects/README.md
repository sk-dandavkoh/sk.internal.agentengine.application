# Example projects

The official examples (vision §58). Each file is the `project.json` part of
a Project Package (`docs/project-format.md` §3): name, description, graph,
triggers, dependencies.

| File | Flow | Needs |
|---|---|---|
| `hello-world.json` | Manual → Input → Set (transform) → Output | nothing |
| `llm.json` | Manual → Input → LLM → Output | credential `openai` |
| `web-research.json` | Manual → Input → Web Search → Set → LLM → Output; read the result as the execution report | `AKASHA_WEB_SEARCH_URL`, credential `openai` |
| `research-agent.json` | Manual → Input → Set → Agent (tools: Web Search, Fetch Web Page, Write File; it decides how often to call them) → Output; the brief lands in `research/brief.md` of the project workspace | `AKASHA_WEB_SEARCH_URL`, `AKASHA_WORKSPACE_DIR`, credential `openai` |
| `lead-generation.json` | Cron (Mon 08:00 UTC) → Set/Pick query → Web Search → Set → LLM qualification → If score ≥ 7 → Pick subject/body → SMTP; otherwise Log | `AKASHA_WEB_SEARCH_URL`, credentials `openai` and `smtp-password`, your SMTP host and recipients |

## Load one

1. `POST /api/v1/projects` with the file's `name` and `description`.
2. `PUT /api/v1/projects/{id}` with `expected_revision`, `name`,
   `description` and `draft` = `{graph, triggers, dependencies}` from the
   file.
3. Adjust the node configuration to your installation: `provider` and
   `model` of `core.llm` and `core.agent`, and host, port, sender and recipients of
   `core.smtp`.
4. `POST /api/v1/projects/{id}/validate`, then `.../publish`.
5. Run it: `POST /api/v1/projects/{id}/triggers/{trigger_id}/invoke` with
   the input shown in the description (`{"name": "World"}`, ...). The cron
   example runs on its schedule.

Inputs: `hello-world` `{"name"}`, `llm` `{"question"}`, `web-research`
`{"topic"}`, `research-agent` `{"topic"}`, `lead-generation` none.

Every example is exercised end to end by
`tests/integration/test_e2e_examples.py`. A change that breaks one is a
regression (architecture §88).
