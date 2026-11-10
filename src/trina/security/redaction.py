"""Redaction before persistence (clause 3.2, security.md section 4).

``redact_record`` and ``redact_text`` are the only constructors of
:class:`RedactedRecord` and :class:`RedactedText`. Writers accept those types only,
so persisting an unredacted record is a type error and not a convention.

Order: structured drops (headers, then named address, identifier and credential
fields), block patterns, token patterns, assignments, dropped identifiers. Redaction
is idempotent: placeholders match no pattern, so redacting a redacted record changes
nothing and counts nothing.

Limits: a secret with no recognisable shape, such as a password in prose, is not
found. The residual-secret audit lowers that risk and cannot remove it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

from trina.security.patterns import (
    HEADER_KIND,
    KINDS,
    PLACEHOLDER_RE,
    REDACTION_STAGES,
    Pattern,
    accepted,
    placeholder,
)

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]

_SEAL = object()

_IP_KEYS = frozenset(
    {
        "ip",
        "ips",
        "ip_address",
        "ipaddress",
        "ip_addr",
        "remote_addr",
        "remote_address",
        "client_address",
        "forwarded_for",
        "x_forwarded_for",
        "forwarded",
    }
)
_ID_KEY_RE = re.compile(
    r"^(?:x_)?(?:installation|install|device|machine|hardware|anonymous|telemetry)_?"
    r"(?:id|uuid|guid|identifier)$"
)
_SECRET_PARTS = frozenset(
    {"password", "passwd", "secret", "token", "apikey", "credential", "credentials"}
)
_SECRET_PAIRS = frozenset(
    {("api", "key"), ("access", "key"), ("private", "key"), ("secret", "key")}
)
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


class RedactionError(TypeError):
    """A record holds a value that is not JSON data, so it cannot be redacted."""

    def __init__(self, path: str, found: type) -> None:
        self.path = path
        super().__init__(f"{path or '<record>'}: cannot redact a value of type {found.__name__}")


def empty_counts() -> dict[str, int]:
    """Zero for every kind, so a kind that never fired is visible as zero."""
    return dict.fromkeys(KINDS, 0)


def sum_counts(counts: Iterable[Mapping[str, int]]) -> dict[str, int]:
    """Counts by kind summed over records, for a build manifest."""
    total = empty_counts()
    for one in counts:
        for kind, value in one.items():
            total[kind] = total.get(kind, 0) + value
    return total


class RedactedText:
    """Text that went through redaction, with its counts by kind."""

    __slots__ = ("_counts", "_text")

    def __init__(self, seal: object, text: str, counts: Mapping[str, int]) -> None:
        if seal is not _SEAL:
            raise TypeError("RedactedText is created by redact_text() only")
        self._text = text
        self._counts = dict(counts)

    @property
    def text(self) -> str:
        return self._text

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    def __repr__(self) -> str:
        return f"RedactedText({len(self._text)} chars, {sum(self._counts.values())} redactions)"


class RedactedRecord:
    """A record that went through redaction, with its counts by kind.

    The content is held as JSON text, so it cannot be changed after redaction;
    ``data`` returns a fresh copy on every call.
    """

    __slots__ = ("_counts", "_json")

    def __init__(self, seal: object, data: dict[str, Json], counts: Mapping[str, int]) -> None:
        if seal is not _SEAL:
            raise TypeError("RedactedRecord is created by redact_record() only")
        self._json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self._counts = dict(counts)

    @property
    def data(self) -> dict[str, Json]:
        loaded: dict[str, Json] = json.loads(self._json)
        return loaded

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    def to_json(self) -> str:
        """The redacted content as compact JSON text, without the counts."""
        return self._json

    def __repr__(self) -> str:
        return f"RedactedRecord({len(self._json)} chars, {sum(self._counts.values())} redactions)"


def _apply(text: str, pattern: Pattern, counts: dict[str, int]) -> str:
    mark = placeholder(pattern.kind)

    def replace(match: re.Match[str]) -> str:
        if not accepted(pattern, match):
            return match.group(0)
        counts[pattern.kind] += 1
        start, end = match.span(pattern.group)
        whole = match.group(0)
        return whole[: start - match.start()] + mark + whole[end - match.start() :]

    return pattern.regex.sub(replace, text)


def _redact_string(text: str, counts: dict[str, int]) -> str:
    for stage in REDACTION_STAGES:
        for pattern in stage:
            text = _apply(text, pattern, counts)
    return text


def _key_parts(key: str) -> list[str]:
    spaced = _CAMEL_RE.sub("_", key)
    return [part for part in re.split(r"[^a-z0-9]+", spaced.lower()) if part]


def _is_header_key(parts: list[str]) -> bool:
    return bool(parts) and parts[-1] == "headers"


def _is_ip_key(parts: list[str]) -> bool:
    return "_".join(parts) in _IP_KEYS or (len(parts) > 1 and parts[-1] in ("ip", "ips"))


def _is_secret_key(parts: list[str]) -> bool:
    if any(part in _SECRET_PARTS for part in parts):
        return True
    return any(pair in _SECRET_PAIRS for pair in zip(parts, parts[1:], strict=False))


def _is_placeholder(value: object) -> bool:
    return isinstance(value, str) and PLACEHOLDER_RE.fullmatch(value) is not None


def _field_count(value: object) -> int:
    """How many header fields a dropped header container held."""
    if isinstance(value, Mapping | list | tuple):
        return len(value)
    return 0 if value is None else 1


def _redact_value(value: object, path: str, counts: dict[str, int]) -> Json:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _redact_string(value, counts)
    if isinstance(value, list | tuple):
        return [_redact_value(item, f"{path}[{i}]", counts) for i, item in enumerate(value)]
    if isinstance(value, Mapping):
        return _redact_mapping(value, path, counts)
    raise RedactionError(path, type(value))


def _redact_mapping(
    mapping: Mapping[str, object], path: str, counts: dict[str, int]
) -> dict[str, Json]:
    result: dict[str, Json] = {}
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise RedactionError(path, type(key))
        where = f"{path}.{key}" if path else key
        parts = _key_parts(key)
        if _is_header_key(parts):
            # Dropped entirely, name and value; there is no allow-list.
            counts[HEADER_KIND] += _field_count(value)
            continue
        dropped = (
            "ip_address"
            if _is_ip_key(parts)
            else "installation_id"
            if _ID_KEY_RE.match("_".join(parts))
            else None
        )
        clean_key = _redact_string(key, counts)
        empty = value is None or value == "" or value == [] or value == {}
        if dropped is not None:
            if empty or _is_placeholder(value):
                result[clean_key] = _redact_value(value, where, counts)
            else:
                counts[dropped] += 1
                result[clean_key] = placeholder(dropped)
        elif _is_secret_key(parts) and isinstance(value, str) and value:
            if _is_placeholder(value):
                result[clean_key] = value
            else:
                counts["assignment"] += 1
                result[clean_key] = placeholder("assignment")
        else:
            result[clean_key] = _redact_value(value, where, counts)
    return result


def redact_text(text: str) -> RedactedText:
    """Redact free text: secret patterns replaced, addresses and identifiers dropped."""
    if not isinstance(text, str):
        raise RedactionError("", type(text))
    counts = empty_counts()
    return RedactedText(_SEAL, _redact_string(text, counts), counts)


def redact_record(record: Mapping[str, object]) -> RedactedRecord:
    """Redact a record of JSON data before it is persisted.

    Header containers are dropped entirely; address and installation-identifier
    fields are replaced by a typed placeholder; a credential-named field keeps its
    name and loses its value; every string, keys included, goes through the text
    patterns. A value that is not JSON data raises RedactionError.
    """
    if not isinstance(record, Mapping):
        raise RedactionError("", type(record))
    counts = empty_counts()
    return RedactedRecord(_SEAL, _redact_mapping(record, "", counts), counts)
