"""The pattern catalogue shared by redaction, the audit, the scanner and the gate.

One catalogue (security.md section 4) so that the redactor, the residual-secret audit,
the artifact scanner and ``scripts/check_hygiene.py`` cannot drift apart. A pattern
names its kind, the group that holds the sensitive value and, where the shape alone
is not enough, a predicate on that value.

Redaction order is fixed: block patterns, token patterns, assignments, then dropped
identifiers. Structured drops (headers and named fields) happen before any of them,
in :mod:`trina.security.redaction`.

Nothing here holds or prints a credential: the module is regular expressions only.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

# Kinds whose value is replaced, kinds that are dropped, and the structured header drop.
REPLACED_KINDS = ("api_key", "cloud_token", "repo_token", "bearer", "private_key", "assignment")
DROPPED_KINDS = ("ip_address", "installation_id")
HEADER_KIND = "header"
KINDS = (*REPLACED_KINDS, *DROPPED_KINDS, HEADER_KIND)

PLACEHOLDER_RE = re.compile(r"\[(?:REDACTED|DROPPED):[a-z_]+\]")


def placeholder(kind: str) -> str:
    """The typed, stable placeholder of a kind. It encodes nothing of the value."""
    if kind in REPLACED_KINDS:
        return f"[REDACTED:{kind}]"
    if kind in DROPPED_KINDS:
        return f"[DROPPED:{kind}]"
    raise ValueError(f"kind {kind!r} has no placeholder")


@dataclass(frozen=True)
class Pattern:
    kind: str
    regex: re.Pattern[str]
    # The group holding the sensitive value; the rest of the match is kept.
    group: int = 0
    # Extra condition on the value, for shapes a regular expression cannot decide.
    accept: Callable[[str], bool] | None = None
    # Exported to the hygiene gate, which scans tracked files, documentation included.
    gate: bool = True


@dataclass(frozen=True)
class PatternMatch:
    kind: str
    start: int
    end: int


def _mixed(value: str) -> bool:
    """Letters and digits together: keeps prose, names and plain numbers out."""
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def _basic_credentials(value: str) -> bool:
    """A basic authorization value decodes to ``user:password``."""
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except (binascii.Error, ValueError):
        return False
    return b":" in decoded


_LITERALS = frozenset({"none", "null", "true", "false", "nil", "undefined"})


def _literal_value(value: str) -> bool:
    """A quoted literal that is a value, not a reference, template or constant."""
    stripped = value.strip()
    return bool(stripped) and stripped[0] not in "$<{%" and stripped.lower() not in _LITERALS


def _env_value(value: str) -> bool:
    return len(value) >= 6 and not value.isdigit() and value.lower() not in _LITERALS


def _ipv6(value: str) -> bool:
    try:
        ipaddress.IPv6Address(value)
    except ValueError:
        return False
    # Scope operators and hex-only words (``a::b``) are valid addresses; real ones
    # carry a digit or several groups.
    return any(c.isdigit() for c in value) or value.count(":") >= 4


# -- block patterns ---------------------------------------------------------------

_KEY_LABEL = r"(?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"

# The body of a block stops at the next BEGIN marker, so a text full of unterminated
# markers is still walked once.
_KEY_BODY = r"(?:[^-]|-(?!----BEGIN ))*?"

BLOCK_PATTERNS: tuple[Pattern, ...] = (
    # The whole block; an unterminated block takes the key material that follows.
    Pattern(
        "private_key",
        re.compile(
            r"-----BEGIN "
            + _KEY_LABEL
            + r"(?:"
            + _KEY_BODY
            + r"-----END "
            + _KEY_LABEL
            + r"|[A-Za-z0-9+/=\s\\]*(?:-----END[A-Z0-9 ]*-*)?)"
        ),
    ),
)

# -- token patterns ---------------------------------------------------------------

_BEARER_VALUE = r"[A-Za-z0-9._~+/-]{20,}=*"
_SIGNED_PARAMS = (
    r"(?:X-Amz-Signature|X-Amz-Security-Token|X-Amz-Credential|X-Goog-Signature"
    r"|Signature|sig)"
)

TOKEN_PATTERNS: tuple[Pattern, ...] = (
    Pattern("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    Pattern("api_key", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    Pattern("api_key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    Pattern("api_key", re.compile(r"\bxox[abeprs]-[A-Za-z0-9-]{10,}")),
    Pattern("cloud_token", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Pattern(
        "cloud_token",
        re.compile(
            r"(?i)aws_?secret_?access_?key\\?[\"']?\s*[:=]\s*\\?[\"']?"
            r"([A-Za-z0-9/+]{40})(?![A-Za-z0-9/+])"
        ),
        group=1,
    ),
    Pattern("repo_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    Pattern("repo_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    Pattern("repo_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    Pattern("repo_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    Pattern("repo_token", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}")),
    Pattern("bearer", re.compile(r"(?i)\bbearer\s+(" + _BEARER_VALUE + ")"), group=1),
    # Any scheme and any value length once the header name is there.
    Pattern(
        "bearer",
        re.compile(
            r"(?i)\bauthorization\\?[\"']?\s*[:=]\s*\\?[\"']?(?:bearer|basic|token)\s+"
            r"([^\s\"',;\\\[]{4,})"
        ),
        group=1,
        gate=False,
    ),
    Pattern(
        "bearer",
        re.compile(r"(?i)\bbasic\s+([A-Za-z0-9+/]{16,}={0,2})(?![A-Za-z0-9+/=])"),
        group=1,
        accept=_basic_credentials,
        gate=False,
    ),
    Pattern(
        "bearer",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    Pattern(
        "bearer",
        re.compile(r"(?i)[?&]" + _SIGNED_PARAMS + r"=([^&\s\"'<>#\[\\]{8,})"),
        group=1,
    ),
    # Credentials in the user part of a URL.
    Pattern(
        "bearer",
        re.compile(r"(?<=://)([^\s/:@\"'<>\[]{1,256}:[^\s/@\"'<>\[]{1,256})@"),
        group=1,
        gate=False,
    ),
)

# -- assignments ------------------------------------------------------------------

_NAME = (
    r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|credentials?|authorization)"
)
# The credential word and the rest of its identifier; ``tokens`` and ``tokenizer`` are
# not credential names. The identifier's start does not matter, and its tail is
# bounded so that a long unbroken run is walked once.
_NAMED = _NAME + r"(?![A-Za-z])[A-Za-z0-9_-]{0,40}+"
_ASSIGN = r"\\?[\"']?\s*(?::=|=>|[:=])\s*"

# An environment name is short; checking that first keeps a long upper-case run linear.
_ENV_NAME = r"(?=[A-Z0-9_]{1,80}=)"

ASSIGNMENT_PATTERNS: tuple[Pattern, ...] = (
    # Environment-file and shell form: an upper-case name at the start of a line.
    Pattern(
        "assignment",
        re.compile(
            r"^[ \t]*(?:export[ \t]+)?"
            + _ENV_NAME
            + r"(?:[A-Z0-9]+_)*(?:PASSWORD|PASSWD|SECRET|TOKEN|KEY|APIKEY)"
            r"(?:_[A-Z0-9]+)*=([^\s\"'#$<\[{(][^\s\"'#]*)",
            re.MULTILINE,
        ),
        group=1,
        accept=_env_value,
        gate=False,
    ),
    # Code and JSON form with a quoted value, also inside an escaped JSON string.
    Pattern(
        "assignment",
        re.compile(r"(?i)" + _NAMED + _ASSIGN + r"\\?([\"'])((?:(?!\\?\1)[^\n])+)\\?\1"),
        group=2,
        accept=_literal_value,
        gate=False,
    ),
    # Unquoted value: shell, query string and YAML forms. A call or an index is code.
    Pattern(
        "assignment",
        re.compile(
            r"(?i)" + _NAMED + r"\\?[\"']?\s*[:=]\s*([^\s\"'`,;&|<>(){}\[\]$#\\]{6,}+)(?![(\[])"
        ),
        group=1,
        accept=_mixed,
        gate=False,
    ),
)

# The assignment shapes strict enough for text that was never redacted. They are the
# shapes of the hygiene gate: a long value mixing letters and digits.
GATE_ASSIGNMENT_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "assignment",
        re.compile(
            r"(?i)(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
            r"[A-Za-z0-9_-]{0,40}+[\"']?\s*[:=]\s*[\"']([A-Za-z0-9+/_.=~-]{16,})[\"']"
        ),
        group=1,
        accept=_mixed,
    ),
    Pattern(
        "assignment",
        re.compile(
            r"^(?:export\s+)?"
            + _ENV_NAME
            + r"[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|KEY)[A-Z0-9_]*="
            r"([^\s\"'#$<\[{(]{16,})\s*$",
            re.MULTILINE,
        ),
        group=1,
        accept=_mixed,
    ),
)

# -- dropped identifiers ----------------------------------------------------------

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_ID_NAME = (
    r"(?:installation|install|device|machine|hardware|anonymous|telemetry)[_-]?"
    r"(?:id|uuid|guid|identifier)"
)

DROP_PATTERNS: tuple[Pattern, ...] = (
    # IPv6 first, so that an embedded IPv4 tail goes with its address.
    Pattern(
        "ip_address",
        re.compile(
            r"(?<![A-Za-z0-9:.])(?:[0-9A-Fa-f]{0,4}:){2,7}"
            r"(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{1,4})?(?![A-Za-z0-9:])"
        ),
        accept=_ipv6,
    ),
    Pattern(
        "ip_address",
        re.compile(r"(?<![\d.])" + _OCTET + r"(?:\." + _OCTET + r"){3}(?!\d|\.\d)"),
    ),
    # Hardware addresses identify a device.
    Pattern(
        "installation_id",
        re.compile(r"(?<![0-9A-Fa-f:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])"),
    ),
    Pattern(
        "installation_id",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])" + _ID_NAME + r"\\?[\"']?\s*[:=]\s*\\?[\"']?"
            r"([A-Za-z0-9][A-Za-z0-9_.:-]{3,})"
        ),
        group=1,
    ),
)

REDACTION_STAGES: tuple[tuple[Pattern, ...], ...] = (
    BLOCK_PATTERNS,
    TOKEN_PATTERNS,
    ASSIGNMENT_PATTERNS,
    DROP_PATTERNS,
)
REDACTION_PATTERNS: tuple[Pattern, ...] = tuple(p for stage in REDACTION_STAGES for p in stage)

# Credential shapes for unredacted text: the artifact scanner's catalogue.
SCAN_PATTERNS: tuple[Pattern, ...] = (
    *BLOCK_PATTERNS,
    *TOKEN_PATTERNS,
    *GATE_ASSIGNMENT_PATTERNS,
)


def accepted(pattern: Pattern, match: re.Match[str]) -> bool:
    """Whether a regular-expression match is a finding of its pattern."""
    value = match.group(pattern.group)
    if not value or PLACEHOLDER_RE.search(value):
        # Already redacted; keeps redaction idempotent.
        return False
    return pattern.accept is None or pattern.accept(value)


def find_matches(text: str, patterns: Sequence[Pattern]) -> Iterator[PatternMatch]:
    """Every accepted match of every pattern, as the span of its sensitive value."""
    for pattern in patterns:
        for match in pattern.regex.finditer(text):
            if accepted(pattern, match):
                yield PatternMatch(
                    pattern.kind, match.start(pattern.group), match.end(pattern.group)
                )


def credential_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Catalogue patterns for ``scripts/check_hygiene.py``: (kind, regex) pairs.

    The gate applies a regex as it is, so only patterns that need no predicate are
    given; the gate's own assignment shapes equal GATE_ASSIGNMENT_PATTERNS.
    """
    return [
        (p.kind, p.regex) for p in (*BLOCK_PATTERNS, *TOKEN_PATTERNS) if p.gate and p.accept is None
    ]
