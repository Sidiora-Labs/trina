"""Canonical serialization and content-derived identities (clause 1.3).

The canonical form is UTF-8 JSON with object keys sorted by code point, no
insignificant whitespace, no duplicate keys, text normalised to NFC, and instants
written as UTC RFC 3339 with exactly six fractional digits and a ``Z`` suffix.
Floats are written in shortest round-trip form and are never part of an identity.
Nothing here depends on the host, the locale, the hash seed or the wall clock.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import NoReturn

from trina.schema.errors import MalformedRecordError

ID_PREFIX = "sha256:"
ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
_INSTANT_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{6})Z")


def normalise_text(record_type: str, path: str, value: object) -> str:
    """Return ``value`` as NFC text; reject a non-string or text UTF-8 cannot carry."""
    if not isinstance(value, str):
        raise MalformedRecordError(record_type, path, "expected a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise MalformedRecordError(record_type, path, "text is not encodable as UTF-8") from None
    return unicodedata.normalize("NFC", value)


def normalise_instant(record_type: str, path: str, value: object) -> datetime:
    """Return an aware datetime in UTC; a naive datetime has no instant and is rejected."""
    if not isinstance(value, datetime):
        raise MalformedRecordError(record_type, path, "expected an instant")
    if value.tzinfo is None or value.utcoffset() is None:
        raise MalformedRecordError(record_type, path, "instant has no UTC offset")
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError):
        raise MalformedRecordError(record_type, path, "instant is out of range") from None


def format_instant(value: datetime) -> str:
    """Write a UTC instant with exactly six fractional digits and a ``Z`` suffix."""
    utc = value.astimezone(UTC)
    return (
        f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}"
        f"T{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}.{utc.microsecond:06d}Z"
    )


def parse_instant(record_type: str, path: str, value: object) -> datetime:
    """Parse the canonical instant form; any other spelling is malformed."""
    if not isinstance(value, str):
        raise MalformedRecordError(record_type, path, "expected an instant string")
    match = _INSTANT_RE.fullmatch(value)
    if match is None:
        raise MalformedRecordError(
            record_type, path, "instant is not UTC RFC 3339 with six fractional digits and Z"
        )
    year, month, day, hour, minute, second, micro = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)
    except ValueError:
        raise MalformedRecordError(record_type, path, "instant is not a valid date") from None


def freeze_json(record_type: str, path: str, value: object) -> object:
    """Validate a free JSON value and return an immutable, NFC-normalised copy.

    Objects become read-only mappings and arrays become tuples, so a frozen record
    cannot change under its identity or its canonical bytes.
    """
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MalformedRecordError(record_type, path, "number is not finite")
        return value
    if isinstance(value, str):
        return normalise_text(record_type, path, value)
    if isinstance(value, Mapping):
        return freeze_map(record_type, path, value)
    if isinstance(value, list | tuple):
        return tuple(
            freeze_json(record_type, f"{path}[{index}]", entry)
            for index, entry in enumerate(value)
        )
    raise MalformedRecordError(record_type, path, "value is not JSON")


def freeze_map(record_type: str, path: str, value: object) -> Mapping[str, object]:
    """Validate a free string-keyed map and return an immutable, NFC-normalised copy."""
    if not isinstance(value, Mapping):
        raise MalformedRecordError(record_type, path, "expected an object")
    frozen: dict[str, object] = {}
    for raw_key, entry in value.items():
        if not isinstance(raw_key, str):
            raise MalformedRecordError(record_type, path, "object key is not a string")
        key = normalise_text(record_type, path, raw_key)
        if key in frozen:
            raise MalformedRecordError(record_type, path, "duplicate key after normalisation")
        frozen[key] = freeze_json(record_type, f"{path}.{key}", entry)
    return MappingProxyType(dict(sorted(frozen.items())))


def thaw_json(value: object) -> object:
    """Return a plain ``dict``/``list`` copy of a frozen JSON value."""
    if isinstance(value, Mapping):
        return {key: thaw_json(entry) for key, entry in value.items()}
    if isinstance(value, list | tuple):
        return [thaw_json(entry) for entry in value]
    return value


def canonical_bytes(value: object) -> bytes:
    """Encode a JSON value built from the contract types to its canonical bytes."""
    text = json.dumps(
        thaw_json(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.encode("utf-8")


def content_identity(tag: str, schema_version: int, content: Mapping[str, object]) -> str:
    """Derive ``sha256:<hex>`` over a domain-separated prefix and canonical content."""
    prefix = f"trina\0{tag}\0v{schema_version}\0".encode()
    return ID_PREFIX + hashlib.sha256(prefix + canonical_bytes(content)).hexdigest()


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"non-finite number {name}")


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, entry in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = entry
    return result


def parse_json(record_type: str, data: str | bytes) -> object:
    """Strictly parse UTF-8 JSON: no duplicate keys, no NaN or infinity, no BOM."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise MalformedRecordError(record_type, "", "bytes are not UTF-8") from None
    elif isinstance(data, str):
        text = data
    else:
        raise MalformedRecordError(record_type, "", "expected JSON text or bytes")
    try:
        return json.loads(
            text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError) as exc:
        # JSONDecodeError is a ValueError; its message holds positions, never content.
        reason = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise MalformedRecordError(record_type, "", f"invalid JSON: {reason}") from None
