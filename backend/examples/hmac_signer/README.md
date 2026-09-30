# Example custom Node: `community.hmac_sign`

This Node signs a JSON payload with a shared secret. A typical use is signing
the body that an HTTP Node then POSTs to a webhook receiver.

It is written the way a developer outside the Engine would write it:
- `node.py` imports only `akasha.sdk`;
- `test_node.py` imports only `akasha.sdk`, `akasha.sdk.testing` and pytest.

`tests/test_sdk.py` enforces this.

It shows the whole SDK surface:

| SDK feature | Where in `node.py` |
|---|---|
| metadata, ports, closed config schema | `HmacSignNode.definition` |
| credential reference resolved at run time | `context.resolve_credential(...)` |
| structured logging without payloads or secrets | `context.logger.info(..., extra=...)` |
| declared metrics | `payloads_signed`, `bytes_signed` |
| declared events | `payload_signed`, `payload_rejected` |
| classified errors with a stable code | `HMAC_PAYLOAD_TOO_LARGE` (VALIDATION) |

Run its tests from `backend/`:

```bash
uv run pytest examples/hmac_signer
```

The same Node running inside the real Engine (registry, publish, runtime,
encrypted credential store, event store, metrics) is covered by
`tests/integration/test_sdk_node_runtime.py`.

See [`docs/sdk.md`](../../../docs/sdk.md) for the SDK guide.
