"""Typed errors of the requirement 1 contract (clause 1.3)."""

from __future__ import annotations


class ContractError(ValueError):
    """Base of every error raised by the contract types and their codecs."""


class SchemaVersionError(ContractError):
    """A record carries a ``schema_version`` this runtime does not know."""

    def __init__(self, record_type: str, found: int, supported: tuple[int, ...]) -> None:
        self.record_type = record_type
        self.found = found
        self.supported = supported
        known = ", ".join(str(v) for v in supported)
        super().__init__(f"{record_type}: unknown schema_version {found} (supported: {known})")


class MalformedRecordError(ContractError):
    """A record is not valid for its declared version.

    Raised for a missing required field, a wrong type, an unknown field, an invalid
    instant, an ``id`` that does not match its content, and bytes that are not strict
    UTF-8 JSON. ``path`` locates the offending field without quoting its value.
    """

    def __init__(self, record_type: str, path: str, reason: str) -> None:
        self.record_type = record_type
        self.path = path
        self.reason = reason
        where = f"{record_type}.{path}" if path else record_type
        super().__init__(f"{where}: {reason}")
