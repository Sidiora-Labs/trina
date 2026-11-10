"""The declared key set of a configuration (clause 11.1).

A schema maps dotted keys to kinds. A key outside it is an error and a key no layer
sets is an error, so a resolved document holds exactly the declared keys. The schema
carries no default values. A package that owns a configuration section declares its
keys in its own code and adds them with ``Schema.extend``.

Kinds are ``str``, ``int``, ``float`` and ``bool``, a ``list[...]`` of one of them, or
a ``map[...]`` from free string keys to one of them.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from trina.config.errors import ConfigValueError, SchemaError

SCALAR_KINDS = ("str", "int", "float", "bool")
KINDS = frozenset(SCALAR_KINDS) | {
    f"{container}[{scalar}]" for container in ("list", "map") for scalar in SCALAR_KINDS
}
SEGMENT_RE = re.compile(r"[a-z][a-z0-9_]*")


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text is not encodable as UTF-8") from None
    return unicodedata.normalize("NFC", value)


def _scalar(kind: str, value: object) -> object:
    """Return ``value`` normalised for a scalar kind; raise ValueError with the reason."""
    if kind == "str":
        return _text(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("expected a boolean")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected an integer")
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a number")
    # One spelling per number: 1, 1.0 and 1e0 are the same float, and -0.0 is 0.0.
    try:
        number = float(value)
    except OverflowError:
        raise ValueError("number is out of range") from None
    if not math.isfinite(number):
        raise ValueError("number is not finite")
    if number != value:
        raise ValueError("integer is not exactly representable as a float")
    return number + 0.0


@dataclass(frozen=True)
class Schema:
    """Dotted keys and their kinds. Immutable; ``extend`` returns a larger schema."""

    fields: Mapping[str, str]
    _tables: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        tables: set[str] = set()
        for key, kind in self.fields.items():
            segments = key.split(".") if isinstance(key, str) else []
            if not segments or not all(SEGMENT_RE.fullmatch(part) for part in segments):
                raise SchemaError(f"invalid key {key!r}")
            if kind not in KINDS:
                raise SchemaError(f"{key}: unknown kind {kind!r}")
            tables.update(".".join(segments[:depth]) for depth in range(1, len(segments)))
        clashes = sorted(tables & set(self.fields))
        if clashes:
            raise SchemaError(f"{clashes[0]} is declared both as a value and as a table")
        object.__setattr__(self, "fields", MappingProxyType(dict(sorted(self.fields.items()))))
        object.__setattr__(self, "_tables", frozenset(tables))

    def extend(self, fields: Mapping[str, str]) -> Schema:
        """Return this schema plus ``fields``; redeclaring a key is an error."""
        repeated = sorted(set(fields) & set(self.fields))
        if repeated:
            raise SchemaError(f"{repeated[0]} is already declared")
        return Schema({**self.fields, **fields})

    def is_table(self, path: str) -> bool:
        """True when ``path`` is a proper prefix of a declared key."""
        return path in self._tables

    def normalise(self, key: str, value: object, layer: str) -> object:
        """Check ``value`` against the kind of ``key`` and return its frozen form."""
        kind = self.fields[key]
        try:
            if kind in SCALAR_KINDS:
                return _scalar(kind, value)
            container, _, rest = kind.partition("[")
            scalar = rest.rstrip("]")
            if container == "list":
                if not isinstance(value, list | tuple):
                    raise ValueError("expected a list")
                return tuple(_scalar(scalar, entry) for entry in value)
            if not isinstance(value, Mapping):
                raise ValueError("expected a table")
            entries: dict[str, object] = {}
            for raw_name, entry in value.items():
                name = _text(raw_name)
                if name in entries:
                    raise ValueError("duplicate entry name after normalisation")
                entries[name] = _scalar(scalar, entry)
            return MappingProxyType(dict(sorted(entries.items())))
        except ValueError as exc:
            raise ConfigValueError(key, layer, str(exc)) from None
