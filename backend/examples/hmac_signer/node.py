"""community.hmac_sign — sign a JSON payload for an outgoing webhook.

An example custom Node written against the public SDK only (``akasha.sdk``),
the way a node developer outside the engine would write it. Typical use:
sign the body an HTTP node then POSTs, so the receiver can verify it came
from this workflow (``X-Signature: sha256=<hex>``).
"""

import hmac
import json

from akasha.sdk import (
    CREDENTIAL_REFERENCE_FORMAT,
    Node,
    NodeConfigSchema,
    NodeDefinition,
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)

DEFAULT_MAX_PAYLOAD_BYTES = 65_536


class HmacSignNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="community.hmac_sign",
            name="HMAC Sign",
            description="Serializes a JSON payload canonically and signs it "
            "with a shared secret (HMAC).",
            version="1.0.0",
            category="security",
        ),
        inputs=(NodeInput("payload", PortType.JSON, description="The value to sign."),),
        outputs=(
            NodeOutput(
                "body",
                PortType.STRING,
                description="Canonical JSON: send exactly these bytes.",
            ),
            NodeOutput(
                "signature",
                PortType.STRING,
                description="'<algorithm>=<hex digest>' of body.",
            ),
        ),
        config_schema=NodeConfigSchema(
            {
                "type": "object",
                "properties": {
                    # A credential reference ("credential:<name>"): the format
                    # is validated and makes forms offer a credential picker.
                    "secret_credential": {
                        "type": "string",
                        "format": CREDENTIAL_REFERENCE_FORMAT,
                    },
                    "algorithm": {"type": "string", "enum": ["sha256", "sha512"]},
                    "max_payload_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1_048_576,
                    },
                },
                "required": ["secret_credential"],
                "additionalProperties": False,
            }
        ),
        metrics=("payloads_signed", "bytes_signed"),
        events=("payload_signed", "payload_rejected"),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        config = context.config
        algorithm: str = config.get("algorithm", "sha256")
        limit: int = config.get("max_payload_bytes", DEFAULT_MAX_PAYLOAD_BYTES)

        # Canonical form: the receiver must be able to reproduce the bytes.
        body = json.dumps(
            context.inputs["payload"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        size = len(body.encode())
        if size > limit:
            context.events.emit("payload_rejected")
            raise NodeError(
                f"payload is {size} bytes, the limit is {limit}",
                kind=NodeErrorKind.VALIDATION,  # retrying cannot shrink it
                code="HMAC_PAYLOAD_TOO_LARGE",
                details={"size": str(size), "limit": str(limit)},
            )

        secret = await context.resolve_credential(config["secret_credential"])
        digest = hmac.new(secret.encode(), body.encode(), algorithm).hexdigest()

        # Sizes and the algorithm only: the body may hold business data and
        # the secret must never be logged.
        context.logger.info(
            "payload_signed", extra={"algorithm": algorithm, "bytes": size}
        )
        context.metrics.increment("payloads_signed")
        context.metrics.increment("bytes_signed", size)
        context.events.emit("payload_signed")
        return NodeResult({"body": body, "signature": f"{algorithm}={digest}"})


def verify(body: str, signature: str, secret: str) -> bool:
    """What the webhook receiver does (constant-time comparison)."""
    algorithm, _, digest = signature.partition("=")
    if algorithm not in ("sha256", "sha512"):
        return False
    expected = hmac.new(secret.encode(), body.encode(), algorithm)
    return hmac.compare_digest(expected.hexdigest(), digest)
