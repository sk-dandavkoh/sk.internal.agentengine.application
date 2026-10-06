"""Central secret redaction (AGENTS §35, backend/AGENTS §29).

One place decides what looks secret; logging, API errors and persisted node
errors all route through it instead of scattering string replacements.

Two structural rules:
- Mapping keys: a value under a sensitive key (``password``, ``api_key``,
  ``Authorization``, ...) is replaced whole, whatever its type.
- Free text: credential-shaped substrings (bearer/basic auth, URL userinfo,
  ``key=value`` with a sensitive key, well-known token formats) are masked.

This is defence in depth for *unknown* secrets. Known values are masked
exactly: resolving a credential registers its plaintext for the current
(async) context via ``register_secret``, so the node that resolved it — its
logs, its errors, its outputs — can never echo it back (Phase 13).
"""

import re
from collections.abc import Mapping
from contextvars import ContextVar

MASK = "[REDACTED]"

_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwords",
        "passwd",
        "pwd",
        "passphrase",
        "secret",
        "secrets",
        "token",
        "apikey",
        "authorization",
        "auth",
        "cookie",
        "credential",
        "credentials",
        "privatekey",
        "signature",
    }
)
# "credential_id", "token_type", "token_count": references and metadata about
# a secret, not secret material.
_NON_SECRET_SUFFIXES = frozenset(
    {"id", "ids", "ref", "reference", "type", "kind", "count", "usage", "total"}
)

# Plurals only where the plural is still secret ("secrets: ..."), so that
# "tokens: 42" (a count) stays readable.
_SENSITIVE_KEY_TEXT = (
    r"[\w-]*(?:passwords?|passwd|pwd|passphrase|secrets?|token|api[_-]?keys?"
    r"|apikeys?|authorization|credentials?|private[_-]?keys?|signature)"
)
_TEXT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host -> scheme://[REDACTED]@host
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"), MASK),
    (re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]{8,}"), rf"\1 {MASK}"),
    # api_key=..., "password": "...", secret: ...
    (
        re.compile(
            # ``credential:<name>`` is a credential *reference*, not a value.
            rf"""(?i)\b(?!credential:[a-z0-9])({_SENSITIVE_KEY_TEXT})(["']?\s*[:=]\s*["']?)[^\s"'&,;}}]+"""
        ),
        rf"\1\2{MASK}",
    ),
    (re.compile(r"\bsk-[\w-]{16,}"), MASK),  # OpenAI/Anthropic-style keys
    (re.compile(r"\bakasha_pat_[\w-]{16,}"), MASK),  # Akasha access tokens
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_\w{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[\w-]{10,}"), MASK),  # Slack
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),  # AWS access key id
    (re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), MASK),  # JWT
)

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def is_sensitive_key(key: str) -> bool:
    parts = [p.lower() for p in _WORD_SPLIT.split(key) if p]
    joined = "".join(parts)
    if not parts or parts[-1] in _NON_SECRET_SUFFIXES:
        return False
    return (
        any(p in _SENSITIVE_WORDS for p in parts)
        or "apikey" in joined
        or "privatekey" in joined
    )


# Shorter values would mask ordinary words; such secrets are weak anyway.
MIN_KNOWN_SECRET_LENGTH = 4

# Per asyncio task (and threads started via ``asyncio.to_thread``): a node
# task sees the secrets it resolved, nothing leaks into unrelated contexts.
_known_secrets: ContextVar[frozenset[str]] = ContextVar(
    "akasha_known_secrets", default=frozenset()
)


def register_secret(value: str) -> None:
    """Mask ``value`` in every redaction within the current context."""
    if len(value) >= MIN_KNOWN_SECRET_LENGTH:
        _known_secrets.set(_known_secrets.get() | {value})


def mask_known_secrets(value: object) -> object:
    """Replace registered secret values inside strings (and mapping keys) of
    a JSON-like value; types and structure are left untouched."""
    known = _known_secrets.get()
    if not known:
        return value
    if isinstance(value, str):
        return _mask_known(value, known)
    if isinstance(value, Mapping):
        return {
            _mask_known(k, known) if isinstance(k, str) else k: mask_known_secrets(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [mask_known_secrets(v) for v in value]
    return value


def _mask_known(text: str, known: frozenset[str]) -> str:
    # Longest first: a secret containing another one is masked whole.
    for secret in sorted(known, key=len, reverse=True):
        text = text.replace(secret, MASK)
    return text


def redact_text(text: str) -> str:
    text = _mask_known(text, _known_secrets.get())
    for pattern, replacement in _TEXT_RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_mapping(data: Mapping[str, object]) -> dict[str, object]:
    return {
        key: MASK if is_sensitive_key(key) else redact(value)
        for key, value in data.items()
    }


def redact(value: object) -> object:
    """Return a redacted, JSON-friendly copy of ``value``.

    Unknown objects are stringified *before* masking, so a secret cannot slip
    through a later ``str()`` (e.g. ``json.dumps(default=str)``).
    """
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_mapping({str(k): v for k, v in value.items()})
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(v) for v in value]
    return redact_text(str(value))
