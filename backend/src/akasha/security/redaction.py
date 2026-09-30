"""Central secret redaction (AGENTS §35, backend/AGENTS §29).

One place decides what looks secret; logging, API errors and persisted node
errors all route through it instead of scattering string replacements.

Two structural rules:
- Mapping keys: a value under a sensitive key (``password``, ``api_key``,
  ``Authorization``, ...) is replaced whole, whatever its type.
- Free text: credential-shaped substrings (bearer/basic auth, URL userinfo,
  ``key=value`` with a sensitive key, well-known token formats) are masked.

This is defence in depth for *unknown* secrets. Masking exact known
credential values belongs to the credential store (Phase 13), which can
extend the text rules at that point.
"""

import re
from collections.abc import Mapping

MASK = "[REDACTED]"

_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "passphrase",
        "secret",
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
            rf"""(?i)\b({_SENSITIVE_KEY_TEXT})(["']?\s*[:=]\s*["']?)[^\s"'&,;}}]+"""
        ),
        rf"\1\2{MASK}",
    ),
    (re.compile(r"\bsk-[\w-]{16,}"), MASK),  # OpenAI/Anthropic-style keys
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


def redact_text(text: str) -> str:
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
