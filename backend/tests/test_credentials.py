"""Credential security without infrastructure: encryption, the Secret
wrapper, the plaintext-in-config guard, node-scoped resolution and
known-value redaction (Phase 13). Persistence/API/runtime: integration."""

import asyncio
import base64
import json
import logging
import os
import pickle
from typing import Any

import pytest
from pydantic import SecretStr

from akasha.application.credentials import CredentialNotFoundError
from akasha.bootstrap import create_container
from akasha.config import Settings
from akasha.domain.credentials import (
    MAX_SECRET_LENGTH,
    Credential,
    CredentialKind,
    PlaintextSecretError,
    Secret,
    credential_references,
    plaintext_secret_paths,
)
from akasha.domain.execution import ExecutionError
from akasha.domain.graph import Graph, NodeInstance
from akasha.domain.graph_validation import ValidationCode, validate_graph
from akasha.domain.node import (
    Node,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)
from akasha.domain.workflow import Workflow, WorkflowDraft
from akasha.errors import ConfigurationError, DomainValidationError
from akasha.runtime.credentials import NodeCredentials
from akasha.runtime.executor import NodeOutcome, execute_node
from akasha.security.crypto import SecretCipher, SecretDecryptionError
from akasha.security.redaction import MASK, redact, redact_text
from tests.support import capture_json_logs, make_context

# Deliberately shapeless: no pattern rule could recognise it, only the
# known-value redaction can.
CANARY = "correct horse battery staple"
REF = "credential:crm"


def cipher() -> SecretCipher:
    return SecretCipher(os.urandom(32))


class FakeSource:
    """In-memory credential store (the real one is covered in integration)."""

    def __init__(self, **secrets: str) -> None:
        self._secrets = secrets
        self.resolved: list[str] = []

    async def resolve(self, reference: str) -> Secret:
        self.resolved.append(reference)
        name = reference.removeprefix("credential:")
        if name == "broken":
            raise SecretDecryptionError("The stored secret cannot be decrypted.")
        if name not in self._secrets:
            raise CredentialNotFoundError("Credential not found.")
        return Secret(self._secrets[name])


# -- encryption -------------------------------------------------------------------


def test_round_trip() -> None:
    c = cipher()
    blob = c.encrypt(CANARY, context="crm")
    assert c.decrypt(blob, context="crm") == CANARY
    assert CANARY.encode() not in blob


def test_same_plaintext_never_yields_the_same_ciphertext() -> None:
    c = cipher()
    assert c.encrypt(CANARY, context="crm") != c.encrypt(CANARY, context="crm")


@pytest.mark.parametrize(
    "tamper",
    [
        lambda b: b[:-1] + bytes([b[-1] ^ 1]),  # flipped tag bit
        lambda b: b[:20] + bytes([b[20] ^ 1]) + b[21:],  # flipped ciphertext bit
        lambda b: b"\x02" + b[1:],  # unknown format version
        lambda b: b[:5],  # truncated
        lambda b: b"",
    ],
)
def test_tampered_ciphertext_is_rejected(tamper: Any) -> None:
    c = cipher()
    with pytest.raises(SecretDecryptionError):
        c.decrypt(tamper(c.encrypt(CANARY, context="crm")), context="crm")


def test_ciphertext_is_bound_to_its_credential() -> None:
    """A blob copied onto another credential's row must not decrypt."""
    c = cipher()
    with pytest.raises(SecretDecryptionError):
        c.decrypt(c.encrypt(CANARY, context="crm"), context="billing")


def test_wrong_key_fails_without_detail() -> None:
    blob = cipher().encrypt(CANARY, context="crm")
    with pytest.raises(SecretDecryptionError) as exc:
        cipher().decrypt(blob, context="crm")
    assert exc.value.details == {}
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_key_from_config_accepts_urlsafe_and_standard_base64() -> None:
    key = os.urandom(32)
    blob = SecretCipher(key).encrypt("x", context="n")
    for encode in (base64.urlsafe_b64encode, base64.b64encode):
        assert (
            SecretCipher.from_config(encode(key).decode()).decrypt(blob, context="n")
            == "x"
        )


@pytest.mark.parametrize(
    "encoded",
    ["", "too-short", base64.b64encode(os.urandom(16)).decode(), "ü" * 44],
)
def test_bad_keys_are_configuration_errors(encoded: str) -> None:
    with pytest.raises(ConfigurationError) as exc:
        SecretCipher.from_config(encoded)
    assert encoded not in exc.value.message or not encoded


def test_cipher_repr_holds_no_key_material() -> None:
    key = os.urandom(32)
    assert key.hex() not in repr(SecretCipher(key))
    assert repr(SecretCipher(key)) == "SecretCipher(AES-256-GCM)"


def test_invalid_key_fails_startup_and_is_not_echoed() -> None:
    bad = "not-a-valid-key-but-secret"
    with pytest.raises(ConfigurationError) as exc:
        create_container(Settings(credential_encryption_key=SecretStr(bad)))
    assert bad not in str(exc.value)


def test_settings_never_print_the_key() -> None:
    key = base64.b64encode(os.urandom(32)).decode()
    settings = Settings(credential_encryption_key=SecretStr(key))
    assert key not in repr(settings)
    assert key not in str(settings.model_dump())
    assert key not in settings.model_dump_json()


def test_no_key_means_no_credential_service() -> None:
    assert create_container(Settings(database_url=None)).credentials is None


# -- Secret -------------------------------------------------------------------------


def test_secret_is_masked_in_every_textual_form() -> None:
    secret = Secret(CANARY)
    for text in (
        str(secret),
        repr(secret),
        f"{secret}",
        f"{secret!r}",
        format(secret),
    ):
        assert CANARY not in text
    assert CANARY not in repr({"value": secret, "items": [secret]})
    assert redact(secret) == redact_text(str(secret))
    assert secret.reveal() == CANARY


def test_secret_refuses_serialization() -> None:
    secret = Secret(CANARY)
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    with pytest.raises(TypeError):
        json.dumps({"v": secret})
    assert CANARY not in json.dumps({"v": secret}, default=str)
    with pytest.raises(TypeError):
        vars(secret)


@pytest.mark.parametrize(
    "value", ["", "x" * (MAX_SECRET_LENGTH + 1)], ids=["empty", "too-long"]
)
def test_secret_bounds(value: str) -> None:
    with pytest.raises(DomainValidationError) as exc:
        Secret(value)
    assert value[:100] not in exc.value.message or not value


# -- credential model / references ---------------------------------------------------


@pytest.mark.parametrize("name", ["", "Upper", "has space", "-x", "a" * 65, "a/b"])
def test_credential_names(name: str) -> None:
    with pytest.raises(DomainValidationError):
        Credential(name=name, kind=CredentialKind.GENERIC)


def test_credential_reference_round_trip() -> None:
    credential = Credential(name="smtp.main", kind=CredentialKind.SMTP)
    assert credential.reference == "credential:smtp.main"


def test_references_are_found_anywhere_in_config() -> None:
    config = {
        "password_credential": "credential:a",
        "header_credentials": {"Authorization": "credential:b"},
        "list": ["credential:c", {"deep": "credential:d"}],
        "not_a_ref": "credential:",
        "text": "credential:e is mentioned in prose",
    }
    assert credential_references(config) == {
        "credential:a",
        "credential:b",
        "credential:c",
        "credential:d",
    }


# -- plaintext guard ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "paths"),
    [
        ({"password": "hunter2"}, [".password"]),
        ({"password_credential": "hunter2"}, [".password_credential"]),
        ({"api_key": "abc"}, [".api_key"]),
        (
            {"headers": {"Authorization": "Bearer abcdefghijkl"}},
            [".headers.Authorization"],
        ),
        ({"headers": {"X-Api-Key": "abc"}}, [".headers.X-Api-Key"]),
        ({"url": "https://bob:hunter2@example.com/"}, [".url"]),
        ({"note": "use sk-abcdefghijklmnopqrstuvwx"}, [".note"]),
        ({"auth": {"user": "bob", "pass": "x"}}, [".auth.user", ".auth.pass"]),
        ({"secrets": ["a", "credential:ok"]}, [".secrets[0]"]),
    ],
)
def test_plaintext_secrets_are_located(
    config: dict[str, Any], paths: list[str]
) -> None:
    assert plaintext_secret_paths(config) == paths


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"password_credential": "credential:smtp"},
        {"header_credentials": {"Authorization": "credential:crm"}},
        {"username": "bob", "host": "mail.example.com", "port": 587},
        {"message": "hello", "url": "https://example.com/?page=2"},
        {"credential_id": "cred_1", "token_count": 42, "max_tokens": 100},
        {"api_key": None, "password": 1234},  # not strings: nothing to leak
    ],
)
def test_clean_configs_pass(config: dict[str, Any]) -> None:
    assert plaintext_secret_paths(config) == []


def test_draft_with_plaintext_secret_is_rejected_without_echoing_it() -> None:
    workflow = Workflow.create("p")
    node = NodeInstance.create("core.smtp", config={"password": CANARY})
    graph = Graph()
    graph.add_node(node)
    with pytest.raises(PlaintextSecretError) as exc:
        workflow.replace_draft(WorkflowDraft(graph=graph), name="p")
    assert exc.value.code == "PLAINTEXT_SECRET"
    assert exc.value.details == {"paths": [f"nodes.{node.id}.config.password"]}
    assert CANARY not in f"{exc.value} {exc.value.details}"
    assert workflow.draft.graph.nodes == {}  # nothing stored


def test_legacy_plaintext_blocks_validation() -> None:
    """Drafts/versions stored before Phase 13 cannot be published or run."""
    node = NodeInstance.create("test.any", config={"api_key": CANARY})
    graph = Graph()
    graph.add_node(node)
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.any", name="Any", description="d", version="1.0.0"
        )
    )
    result = validate_graph(graph, {"test.any": definition})
    (issue,) = result.by_code(ValidationCode.PLAINTEXT_SECRET)
    assert CANARY not in issue.message


# -- known-value redaction -----------------------------------------------------------


async def _in_task[T](coro: Any) -> T:
    """Run like the runtime does: in a task with its own copied context."""
    result: T = await asyncio.create_task(coro)
    return result


async def test_resolved_secret_is_masked_only_in_the_resolving_context() -> None:
    async def resolve_and_redact() -> str:
        creds = NodeCredentials(FakeSource(crm=CANARY), {"c": REF})
        value = await creds.resolve(REF)
        return redact_text(f"login with {value} failed")

    assert await _in_task(resolve_and_redact()) == f"login with {MASK} failed"
    # Nothing leaked into the caller's (or a sibling task's) context.
    assert redact_text(CANARY) == CANARY


async def test_short_secrets_are_not_registered() -> None:
    async def resolve() -> str:
        creds = NodeCredentials(FakeSource(crm="abc"), {"c": REF})
        await creds.resolve(REF)
        return redact_text("abc abc")

    assert await _in_task(resolve()) == "abc abc"


def test_references_stay_readable_in_redacted_text() -> None:
    assert redact_text("credential not found: credential:crm") == (
        "credential not found: credential:crm"
    )
    assert "hunter2" not in redact_text("credential: hunter2")
    assert "hunter2" not in redact_text("password:hunter2")


# -- node-scoped resolution -----------------------------------------------------------


async def test_only_references_in_the_node_config_resolve() -> None:
    source = FakeSource(crm=CANARY, billing="other-secret")
    creds = NodeCredentials(source, {"header_credentials": {"A": REF}})
    with pytest.raises(NodeError) as exc:
        await creds.resolve("credential:billing")
    assert exc.value.code == "CREDENTIAL_NOT_REFERENCED"
    assert exc.value.kind is NodeErrorKind.CONFIGURATION
    assert source.resolved == []  # refused before the store was touched


@pytest.mark.parametrize(
    ("ref", "code", "kind"),
    [
        ("credential:missing", "CREDENTIAL_NOT_FOUND", NodeErrorKind.CONFIGURATION),
        ("credential:broken", "CREDENTIAL_UNAVAILABLE", NodeErrorKind.PERMANENT),
    ],
)
async def test_resolution_failures_are_node_errors(
    ref: str, code: str, kind: NodeErrorKind
) -> None:
    creds = NodeCredentials(FakeSource(), {"c": ref})
    with pytest.raises(NodeError) as exc:
        await creds.resolve(ref)
    assert (exc.value.code, exc.value.kind) == (code, kind)
    assert exc.value.__cause__ is None


# -- executor: a node that tries every way to leak what it resolved -----------------


class LeakyNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="test.leaky", name="Leaky", description="d", version="1.0.0"
        ),
        outputs=(
            NodeOutput("text", PortType.STRING),
            NodeOutput("doc", PortType.JSON),
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        assert context.credentials is not None
        value = await context.credentials.resolve(REF)
        context.logger.warning("resolved %s", value, extra={"echo": value})
        try:
            raise RuntimeError(f"boom {value}")
        except RuntimeError:
            context.logger.exception("with traceback")
        if context.config.get("fail"):
            raise NodeError(
                f"server said: {value}",
                kind=NodeErrorKind.PERMANENT,
                details={"reply": value},
            )
        if context.config.get("crash"):
            raise ValueError(value)
        return NodeResult(
            {"text": f"echo {value}", "doc": {"k": [value, 1], value: "as key"}}
        )


def leaky_context(**config: Any) -> NodeExecutionContext:
    return make_context(
        LeakyNode(),
        config={"crm": REF, **config},
        credentials=NodeCredentials(FakeSource(crm=CANARY), {"crm": REF}),
    )


async def run_leaky(**config: Any) -> tuple[NodeOutcome, str]:
    with capture_json_logs() as lines:
        outcome: NodeOutcome = await _in_task(
            execute_node(LeakyNode, leaky_context(**config), limit=None)
        )
    return outcome, json.dumps(lines)


async def test_outputs_and_logs_never_carry_a_resolved_secret() -> None:
    outcome, logs = await run_leaky()
    assert isinstance(outcome, NodeResult)
    assert outcome.outputs == {
        "text": f"echo {MASK}",
        "doc": {"k": [MASK, 1], MASK: "as key"},
    }
    assert "resolved" in logs and "with traceback" in logs
    assert CANARY not in logs


@pytest.mark.parametrize("config", [{"fail": True}, {"crash": True}])
async def test_errors_never_carry_a_resolved_secret(config: dict[str, Any]) -> None:
    outcome, logs = await run_leaky(**config)
    assert isinstance(outcome, ExecutionError)
    assert CANARY not in f"{outcome.message} {outcome.details} {outcome!r}"
    assert CANARY not in logs


def test_logging_a_secret_object_prints_the_mask(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    logging.getLogger("akasha.test").info("value=%s", Secret(CANARY))
    assert CANARY not in caplog.text
