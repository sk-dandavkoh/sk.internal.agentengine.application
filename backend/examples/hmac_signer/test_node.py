"""Tests for community.hmac_sign, written as a node developer would: only
``akasha.sdk`` / ``akasha.sdk.testing`` and pytest, no engine internals."""

import hashlib
import hmac
import logging

import pytest

from akasha.sdk import (
    NodeConfigurationError,
    NodeError,
    NodeErrorKind,
    NodeValidationError,
    check_node_contract,
)
from akasha.sdk.testing import run_node
from examples.hmac_signer import HmacSignNode, verify

REF = "credential:webhook-secret"
CANARY = "whsec-example-3b7f-never-log-me"
CONFIG = {"secret_credential": REF}


async def sign(payload: object, **config: object) -> tuple[str, str]:
    run = await run_node(
        HmacSignNode(),
        config=CONFIG | config,
        inputs={"payload": payload},
        credentials={REF: CANARY},
    )
    return run.outputs["body"], run.outputs["signature"]


def test_node_satisfies_the_contract() -> None:
    check_node_contract(HmacSignNode)
    assert HmacSignNode.definition.node_id == "community.hmac_sign"


async def test_signs_the_canonical_body() -> None:
    body, signature = await sign({"b": 1, "a": "ä"})

    assert body == '{"a":"ä","b":1}'
    expected = hmac.new(CANARY.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert signature == f"sha256={expected}"
    assert verify(body, signature, CANARY)
    assert not verify(body, signature, "wrong-secret")


async def test_key_order_does_not_change_the_signature() -> None:
    assert await sign({"a": 1, "b": 2}) == await sign({"b": 2, "a": 1})


async def test_algorithm_is_configurable() -> None:
    body, signature = await sign([1, 2], algorithm="sha512")
    assert signature.startswith("sha512=")
    assert verify(body, signature, CANARY)


async def test_reports_metrics_and_events() -> None:
    run = await run_node(
        HmacSignNode(),
        config=CONFIG,
        inputs={"payload": {"x": 1}},
        credentials={REF: CANARY},
    )
    assert run.events == ["payload_signed"]
    assert run.metrics == {"payloads_signed": 1, "bytes_signed": 7}


async def test_logs_sizes_but_never_the_secret_or_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        await sign({"customer": "ACME-private"})

    [record] = [r for r in caplog.records if r.getMessage() == "payload_signed"]
    assert record.__dict__["bytes"] == 27
    assert record.__dict__["algorithm"] == "sha256"
    logged = str(record.__dict__)
    assert CANARY not in logged
    assert "ACME-private" not in logged


@pytest.mark.parametrize(
    "config",
    [
        {},  # the credential is required
        {"secret_credential": REF, "algorithm": "md5"},
        {"secret_credential": REF, "max_payload_bytes": 0},
        {"secret_credential": REF, "sign_twice": True},  # unknown key
        {"secret_credential": CANARY},  # plaintext secret, not a reference
    ],
)
async def test_rejects_invalid_configuration(config: dict[str, object]) -> None:
    with pytest.raises(NodeConfigurationError):
        await run_node(HmacSignNode(), config=config, inputs={"payload": 1})


async def test_payload_input_is_required() -> None:
    with pytest.raises(NodeValidationError):
        await run_node(HmacSignNode(), config=CONFIG, credentials={REF: CANARY})


async def test_oversized_payload_is_a_validation_error() -> None:
    with pytest.raises(NodeError) as caught:
        await sign("x" * 20, max_payload_bytes=10)

    error = caught.value
    assert error.kind is NodeErrorKind.VALIDATION
    assert error.code == "HMAC_PAYLOAD_TOO_LARGE"
    assert not error.retryable
    assert error.details == {"size": "22", "limit": "10"}


async def test_unknown_credential_is_a_configuration_error() -> None:
    with pytest.raises(NodeError) as caught:
        await run_node(
            HmacSignNode(), config=CONFIG, inputs={"payload": 1}, credentials={}
        )
    assert caught.value.kind is NodeErrorKind.CONFIGURATION
    assert caught.value.code == "CREDENTIAL_NOT_FOUND"


async def test_engine_without_credential_store() -> None:
    with pytest.raises(NodeError) as caught:
        await run_node(HmacSignNode(), config=CONFIG, inputs={"payload": 1})
    assert caught.value.code == "CREDENTIALS_UNAVAILABLE"
