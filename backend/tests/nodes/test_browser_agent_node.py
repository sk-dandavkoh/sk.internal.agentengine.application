"""core.browser_agent against a local site and a scripted model: the model
decides every action from what it sees, inside one browser session per run;
the destination guard, the domain allowlist and the step limit still hold.
Real Chromium (skipped without it, like core.browser), never the internet."""

import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

import pytest

from akasha.domain.ai import (
    FinishReason,
    LLMRequest,
    LLMResponse,
    Role,
    TokenUsage,
    ToolCall,
)
from akasha.domain.node import (
    CredentialResolver,
    NetworkPolicy,
    NodeError,
    NodeServices,
)
from akasha.nodes.agent import tool_parameters
from akasha.nodes.browser_agent import BrowserAgentNode
from tests.nodes.test_browser_node import _requires_chromium
from tests.support import RunNode

LOGIN = """<!doctype html><html><head><title>Sign in</title></head><body>
<form method="post" action="/login">
<input name="user" aria-label="User name">
<input name="pass" type="password" aria-label="Password">
<button type="submit">Sign in</button>
</form><a href="/about">About us</a></body></html>"""


@dataclass
class Site:
    base: str
    port: int
    paths: list[str] = field(default_factory=list)


def _handler(site: Site) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            """Quiet."""

        def send(self, body: str, status: int = 200, **headers: str) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            site.paths.append(self.path)
            cookies = self.headers.get("Cookie", "")
            match self.path:
                case "/login":
                    self.send(LOGIN)
                case "/loop":
                    self.send("", status=302, Location="/loop")
                case "/account":
                    signed_in = "sid=ada" in cookies and "theme=dark" in cookies
                    self.send(
                        "<title>Account</title><p>Welcome Ada, balance 42</p>"
                        if signed_in
                        else "<title>Account</title><p>Please sign in</p>"
                    )
                case _:
                    self.send("<title>About</title><p>We sell widgets.</p>")

        def do_POST(self) -> None:
            site.paths.append(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode())
            if form.get("user") == ["ada"] and form.get("pass") == ["s3cret"]:
                # As real logins do: the session cookie (two of them) arrives
                # on the redirect, not on the page it leads to.
                self.send_response(303)
                self.send_header("Location", "/account")
                self.send_header("Set-Cookie", "sid=ada; Path=/")
                self.send_header("Set-Cookie", "theme=dark; Path=/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send("<title>Sign in</title><p>Wrong password</p>")

    return Handler


@pytest.fixture
def site() -> Iterator[Site]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = int(server.server_address[1])
    state = Site(f"http://127.0.0.1:{port}", port)
    server.RequestHandlerClass = _handler(state)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


# -- a model that reads the page ------------------------------------------------

Observation = dict[str, Any]
Policy = Callable[[Observation], ToolCall | str]


def ref(page: Observation, label: str) -> int:
    """The ref of the element whose label is ``label``, as a model would
    pick it from the element list."""
    found: int = next(e["ref"] for e in page["elements"] if e["label"] == label)
    return found


@dataclass
class ScriptedModel:
    """Decides each step from the latest page it was shown."""

    policies: list[Policy]
    requests: list[LLMRequest] = field(default_factory=list)

    async def complete(
        self, request: LLMRequest, *, credentials: CredentialResolver | None = None
    ) -> LLMResponse:
        self.requests.append(request)
        page = json.loads(request.messages[-1].content or "{}")
        decision = self.policies[len(self.requests) - 1](page)
        calls = (decision,) if isinstance(decision, ToolCall) else ()
        return LLMResponse(
            provider="openai",
            model="gpt-test",
            content=decision if isinstance(decision, str) else None,
            tool_calls=calls,
            usage=TokenUsage(input_tokens=10, output_tokens=2),
            finish_reason=FinishReason.TOOL_CALLS if calls else FinishReason.STOP,
        )


def act(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(f"c-{name}", name, arguments)


CONFIG = {"provider": "openai", "model": "gpt-test"}
TRUST_SITE = NetworkPolicy(allowed_hosts=frozenset({"127.0.0.1"}))


async def browse(
    run_node: RunNode,
    model: ScriptedModel,
    url: str,
    task: str = "What is Ada's balance?",
    **config: Any,
) -> dict[str, Any]:
    result = await run_node(
        BrowserAgentNode(),
        config=CONFIG | config,
        inputs={"task": task, "url": url},
        services=NodeServices(llm=model, network=TRUST_SITE),
    )
    return dict(result.outputs)


# -- behaviour -------------------------------------------------------------------


async def test_signs_in_and_reads_the_account_in_one_session(
    run_node: RunNode, site: Site
) -> None:
    model = ScriptedModel(
        [
            lambda page: act("type", ref=ref(page, "User name"), text="ada"),
            lambda page: act(
                "type", ref=ref(page, "Password"), text="s3cret", submit=True
            ),
            lambda page: act("navigate", url=f"{site.base}/about"),
            # A fresh request: only the browser's own cookie jar signs it in.
            lambda page: act("navigate", url=f"{site.base}/account"),
            lambda page: f"Ada's balance is {page['text'].split()[-1]}",
        ]
    )
    out = await browse(run_node, model, f"{site.base}/login")

    assert out["answer"] == "Ada's balance is 42"
    # The redirect after the login is followed like a browser does: the
    # page knows its real URL, and both cookies of the redirect stuck.
    assert out["url"] == f"{site.base}/account"
    assert [(s["action"], s["ok"]) for s in out["steps"]] == [
        ("navigate", True),
        ("type", True),
        ("type", True),
        ("navigate", True),
        ("navigate", True),
    ]
    assert site.paths.count("/account") == 2
    assert out["metadata"]["model_calls"] == 5
    assert out["metadata"]["usage"]["total_tokens"] == 60
    # The task is the user's turn; every page is a tool result.
    first = model.requests[0].messages
    assert first[1].role is Role.USER and first[1].content == "What is Ada's balance?"
    assert first[-1].role is Role.TOOL and "Sign in" in (first[-1].content or "")
    assert {t.name for t in model.requests[0].tools} == {
        "navigate",
        "click",
        "type",
        "select",
        "press",
        "back",
    }


async def test_a_failed_action_is_shown_to_the_model_and_the_run_goes_on(
    run_node: RunNode, site: Site
) -> None:
    seen: list[Observation] = []

    def after_error(page: Observation) -> ToolCall:
        seen.append(page)
        return act("click", ref=ref(page, "About us"))

    model = ScriptedModel(
        [
            lambda page: act("click", ref=999),
            after_error,
            lambda page: page["title"],
        ]
    )
    out = await browse(run_node, model, f"{site.base}/login")
    assert seen[0]["error"]["code"] == "BROWSER_STEP_FAILED"
    assert out["answer"] == "About"
    assert [s["ok"] for s in out["steps"]] == [True, False, True]


async def test_private_destinations_stay_blocked(run_node: RunNode, site: Site) -> None:
    """The model may go anywhere public, never where the operator's policy
    forbids: localhost is not the trusted 127.0.0.1."""
    seen: list[Observation] = []

    def try_localhost(page: Observation) -> ToolCall:
        return act("navigate", url=f"http://localhost:{site.port}/account")

    def report(page: Observation) -> str:
        seen.append(page)
        return "blocked"

    out = await browse(
        run_node, ScriptedModel([try_localhost, report]), f"{site.base}/about"
    )
    assert out["answer"] == "blocked"
    assert seen[0]["error"]["code"] == "DESTINATION_BLOCKED"
    assert "/account" not in site.paths


async def test_allowed_domains_limit_where_the_model_may_go(
    run_node: RunNode, site: Site
) -> None:
    seen: list[Observation] = []

    def leave(page: Observation) -> ToolCall:
        return act("navigate", url="https://example.com/")

    def report(page: Observation) -> str:
        seen.append(page)
        return "stayed"

    out = await browse(
        run_node,
        ScriptedModel([leave, report]),
        f"{site.base}/about",
        allowed_domains=["127.0.0.1"],
    )
    assert out["answer"] == "stayed"
    assert seen[0]["error"]["code"] == "BROWSER_DOMAIN_NOT_ALLOWED"
    with pytest.raises(NodeError) as exc:
        await browse(
            run_node,
            ScriptedModel([]),
            f"{site.base}/about",
            allowed_domains=["example.com"],
        )
    assert exc.value.code == "BROWSER_DOMAIN_NOT_ALLOWED"


async def test_a_redirect_loop_is_refused_and_reported(
    run_node: RunNode, site: Site
) -> None:
    seen: list[Observation] = []

    def report(page: Observation) -> str:
        seen.append(page)
        return "looping"

    model = ScriptedModel(
        [lambda page: act("navigate", url=f"{site.base}/loop"), report]
    )
    out = await browse(run_node, model, f"{site.base}/about")
    assert out["answer"] == "looping"
    assert seen[0]["error"]["code"] == "HTTP_TOO_MANY_REDIRECTS"
    assert site.paths.count("/loop") == 6  # the first request and 5 redirects


async def test_max_steps_ends_a_run_that_never_answers(
    run_node: RunNode, site: Site
) -> None:
    model = ScriptedModel([lambda page: act("back")] * 3)
    with pytest.raises(NodeError) as exc:
        await browse(run_node, model, f"{site.base}/about", max_steps=3)
    assert exc.value.code == "BROWSER_AGENT_MAX_STEPS"
    assert len(model.requests) == 3


async def test_without_an_llm_the_node_is_not_configured(run_node: RunNode) -> None:
    with pytest.raises(NodeError) as exc:
        await run_node(
            BrowserAgentNode(),
            config=CONFIG,
            inputs={"task": "x", "url": "https://example.com/"},
        )
    assert exc.value.code == "LLM_NOT_CONFIGURED"


def test_core_agent_can_delegate_to_it_with_task_and_url_only() -> None:
    params = tool_parameters(BrowserAgentNode.definition)
    assert set(params["properties"]) == {"task", "url"}
    assert params["required"] == ["task", "url"]
