"""Central secret redaction: structural (keys) and textual (credential shapes)."""

import pytest

from akasha.security.redaction import MASK, is_sensitive_key, redact, redact_text

CASES = [
    # (text, the secret part that must disappear)
    ("GET https://admin:hunter2@db.internal/x", "hunter2"),
    ("Authorization: Bearer abcdefgh12345678", "abcdefgh12345678"),
    ("header basic dXNlcjpodW50ZXIy", "dXNlcjpodW50ZXIy"),
    ("connecting with api_key=canary-value-1", "canary-value-1"),
    ('{"password": "hunter2"}', "hunter2"),
    ("client_secret: s3cr3t-value", "s3cr3t-value"),
    ("X-Api-Key=abc123def", "abc123def"),
    ("refresh_token='tok-value-1'", "tok-value-1"),
    ("key sk-ant-api03-abcdefghijklmnop", "sk-ant-api03-abcdefghijklmnop"),
    ("sk-live-hunter2-do-not-leak in text", "sk-live-hunter2-do-not-leak"),
    ("pat ghp_abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdefghijklmnopqrstuvwxyz0123"),
    ("slack xoxb-1234567890-abcdef", "xoxb-1234567890-abcdef"),
    ("aws AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    (
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w",
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
    ),
    ("smtp login failed: user=bob password=hunter2", "hunter2"),
]


@pytest.mark.parametrize(("text", "secret"), CASES)
def test_credential_shaped_text_is_masked(text: str, secret: str) -> None:
    out = redact_text(text)
    assert secret not in out
    assert MASK in out


@pytest.mark.parametrize(
    "text",
    [
        "node exceeded its timeout of 5.0s",
        "https://example.com/path?page=1",
        "tokens: 42",
        "token validation failed",
        "unknown input ports: ['text']",
        "node raised an unexpected RuntimeError",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "API_KEY",
        "apiKey",
        "x-api-key",
        "Authorization",
        "client_secret",
        "access_token",
        "refreshToken",
        "db_password",
        "private_key",
        "Cookie",
        "smtp_credentials",
    ],
)
def test_sensitive_keys(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "credential_id",
        "token_count",
        "token_type",
        "max_tokens",
        "author",
        "execution_id",
        "correlation_id",
        "event",
        "message",
    ],
)
def test_non_sensitive_keys(key: str) -> None:
    assert not is_sensitive_key(key)


def test_nested_structures_are_redacted_by_key_and_by_text() -> None:
    class Opaque:
        def __str__(self) -> str:
            return "conn password=hunter2"

    data = {
        "config": {
            "headers": {"Authorization": "anything at all", "Accept": "json"},
            "items": [{"token": 123}, "url https://u:p4ss@h/x"],
        },
        "count": 3,
        "ok": True,
        "none": None,
        "obj": Opaque(),
        "tags": ("a", "b"),
    }

    assert redact(data) == {
        "config": {
            "headers": {"Authorization": MASK, "Accept": "json"},
            # Non-string secrets under a sensitive key are masked too.
            "items": [{"token": MASK}, f"url https://{MASK}@h/x"],
        },
        "count": 3,
        "ok": True,
        "none": None,
        # Stringified *before* masking: no later str() can leak it.
        "obj": f"conn password={MASK}",
        "tags": ["a", "b"],
    }
