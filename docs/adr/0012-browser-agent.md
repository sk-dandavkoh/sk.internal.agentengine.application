# ADR 0012: A browser the model operates itself

Status: accepted (2026-10-11)

## Context

`core.browser` runs steps the workflow author wrote: as an agent tool, the
model chooses only the URL and the values typed into fixed fields (ADR
0005). That keeps the model from clicking where the author did not intend,
but it cannot browse: it cannot follow what it sees, sign in, or work
through a site the author did not script. Each call is also a fresh
browser, so nothing (a login) carries from one call to the next.

The operator asked for an agent that can use the browser without those
restrictions.

## Decision

1. **A new node, `core.browser_agent`.** Inputs `task` and `url`; config
   is a model (`provider`, `model`, `credential`, ...), optional
   `instructions`, `max_steps` (default 30, at most 100), `allowed_domains`
   (empty: any public site) and `max_page_chars`. Outputs `answer`, the
   last `url`, every action in `steps`, and model usage and cost in
   `metadata`.
2. **The model decides every action.** After each action it sees the page:
   URL, title, visible text (capped) and up to 150 usable elements, each
   with a number (`ref`). It answers with tools: `navigate`, `click`,
   `type` (optionally pressing Enter), `select`, `press`, `back`. A reply
   without a tool call is the answer. A failed action is shown to the
   model, not fatal.
3. **One session per node attempt.** The attempt owns one browser process
   and context for all its actions, so logins and multi-page forms work;
   nothing outlives the attempt. The node's timeout, cancellation and
   retries are the runtime's.
4. **What stays enforced.** The isolation of `core.browser`, shared code:
   every request through the engine's guarded client (SSRF, private
   networks, DNS pinning, size caps), no downloads, uploads or service
   workers, a fresh profile per attempt, at most 3000 requests per
   attempt, 15 s per action. Page content reaches the model only as tool
   results.
5. **Usable as a core.agent tool.** Its only inputs are `task` and `url`,
   so a research agent can delegate "find X on Y" to it. Depth stays
   bounded: the browser agent offers no tools of its own.
6. **Page loads follow redirects like a browser.** Chromium does not route
   the next hop of a fulfilled redirect, so `core.browser` used to follow
   redirects inside the engine: the page kept its first URL, and cookies
   set on a redirect (the usual login) never reached the browser. Now each
   hop of a top-level page load is answered with a page that replaces
   itself with the next URL: Chromium requests it through the guard again,
   with the right URL and each hop's cookies under its own origin (at most
   5 hops, `HTTP_TOO_MANY_REDIRECTS`). Several `Set-Cookie` headers of one
   response are passed on separately instead of joined into one broken
   cookie. Both fixes apply to `core.browser` too.

## Alternatives

- **Let core.agent pass `steps` to core.browser.** Small, but every call
  is a fresh browser (no login across calls) and the model would guess
  selectors for a page it has not seen.
- **Browser tools inside core.agent with a session per agent run.** Ties
  core.agent to the browser and gives every agent the session machinery;
  a separate node keeps both simple and is useful on its own.
- **Human approval before submitting forms.** Safer against prompt
  injection, but no longer autonomous; declined. It can be built from the
  existing `core.human` node around the browser agent where needed.

## Consequences

- Prompt injection is the open risk: a page can ask the model to do
  things, and the model may comply within what the browser can reach.
  Mitigations are the operator's: `allowed_domains`, which credentials a
  workflow hands the model (none are typed unless the task contains
  them), and the network policy.
- ponytail: elements inside iframes and shadow DOM are not listed; a
  page's own redirected requests (scripts, XHR) are still followed by the
  engine, so cookies set on their intermediate hops stay with it.
- The image ships Playwright and Chromium (headless shell and its system
  libraries, about 1 GB), so both browser nodes work in a default
  deployment; the deployment test launches Chromium in the worker as the
  engine user. Outside Docker the browser stays an optional extra; without
  it the nodes fail with `BROWSER_NOT_CONFIGURED`.
