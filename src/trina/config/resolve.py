"""Layered resolution to one frozen document with a digest (clause 11.1).

Layers are applied in order: ``configs/base/``, a stage file, a host profile, then
command-line overrides. A later layer replaces the whole value of a key; lists and
maps are replaced, not merged. The result is the only thing a run reads.

Canonical form is the contract codec's: UTF-8 JSON, keys sorted, no insignificant
whitespace, text in NFC, each number in one spelling. ``digest`` is the sha256 of
those bytes in hex, and ``config.resolved.json`` holds exactly those bytes.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from trina.config.errors import (
    ConfigError,
    ConfigValueError,
    LayerError,
    MissingKeyError,
    UnknownKeyError,
)
from trina.config.schema import SEGMENT_RE, Schema
from trina.schema.canonical import canonical_bytes, freeze_map, parse_json
from trina.schema.errors import MalformedRecordError

OVERRIDES_LAYER = "overrides"


@dataclass(frozen=True)
class Layer:
    """One configuration layer: named parts, each a table read from one source."""

    name: str
    parts: tuple[tuple[str, Mapping[str, object]], ...]

    @classmethod
    def from_table(cls, name: str, table: Mapping[str, object]) -> Layer:
        return cls(name, ((name, table),))


def _read_toml(layer: str, path: Path) -> Mapping[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise LayerError(layer, f"cannot read {path.name} ({type(exc).__name__})") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        # The decoder's message holds a position, never file content.
        raise LayerError(layer, f"{path.name} is not valid TOML: {exc}") from None


def load_layer(path: Path) -> Layer:
    """Read a layer from a TOML file, or from every ``*.toml`` file under a directory.

    Files of a directory layer are peers: a key set by two of them is an error.
    """
    name = str(path)
    if not path.is_dir():
        return Layer(name, ((name, _read_toml(name, path)),))
    files = sorted(file.relative_to(path).as_posix() for file in path.rglob("*.toml"))
    if not files:
        raise LayerError(name, "directory holds no .toml file")
    return Layer(name, tuple((f"{name}/{file}", _read_toml(name, path / file)) for file in files))


def _insert(table: dict[str, object], key: str, value: object) -> None:
    *parents, leaf = key.split(".")
    for part in parents:
        child = table.setdefault(part, {})
        assert isinstance(child, dict)
        table = child
    table[leaf] = value


def parse_overrides(schema: Schema, items: Sequence[str]) -> Layer:
    """Build the last layer from command-line ``key=value`` items.

    The key is a full dotted key of the schema. The value of a ``str`` key is taken
    verbatim; any other value is written as a TOML value, for example ``8``, ``0.5``,
    ``true`` or ``[1, 2]``.
    """
    table: dict[str, object] = {}
    seen: set[str] = set()
    for item in items:
        key, sep, text = item.partition("=")
        key = key.strip()
        if not sep:
            raise LayerError(OVERRIDES_LAYER, "an override is not of the form key=value")
        if key not in schema.fields:
            raise UnknownKeyError(key, OVERRIDES_LAYER)
        if key in seen:
            raise LayerError(OVERRIDES_LAYER, f"{key} is set twice")
        seen.add(key)
        value: object = text
        if schema.fields[key] != "str":
            try:
                value = tomllib.loads(f"value = {text}")["value"]
            except (tomllib.TOMLDecodeError, KeyError):
                raise ConfigValueError(key, OVERRIDES_LAYER, "not a TOML value") from None
        _insert(table, key, value)
    return Layer.from_table(OVERRIDES_LAYER, table)


def _flatten(
    schema: Schema, source: str, table: Mapping[str, object], prefix: str = ""
) -> dict[str, object]:
    """Map a nested table to dotted keys, rejecting anything the schema does not declare."""
    flat: dict[str, object] = {}
    for name, value in table.items():
        path = f"{prefix}{name}"
        if not isinstance(name, str) or not SEGMENT_RE.fullmatch(name):
            raise UnknownKeyError(path, source)
        if path in schema.fields:
            flat[path] = schema.normalise(path, value, source)
        elif not schema.is_table(path):
            raise UnknownKeyError(path, source)
        elif not isinstance(value, Mapping):
            raise ConfigValueError(path, source, "expected a table")
        else:
            flat.update(_flatten(schema, source, value, f"{path}."))
    return flat


@dataclass(frozen=True)
class ResolvedConfig:
    """The one frozen document a run starts from, with its digest.

    ``document`` is a read-only nested mapping. ``sources`` names the layer part that
    set each key; it explains the document and is not part of it or of the digest.
    """

    document: Mapping[str, object]
    sources: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            frozen = freeze_map("config", "", self.document)
        except MalformedRecordError as exc:
            raise ConfigError(f"resolved document: {exc.path}: {exc.reason}") from None
        object.__setattr__(self, "document", frozen)
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))
        object.__setattr__(self, "digest", hashlib.sha256(canonical_bytes(frozen)).hexdigest())

    def to_bytes(self) -> bytes:
        """The canonical bytes; their sha256 is ``digest``."""
        return canonical_bytes(self.document)

    @classmethod
    def from_bytes(cls, data: bytes) -> ResolvedConfig:
        """Reload a frozen document; bytes that are not its canonical form are refused."""
        try:
            document = parse_json("config", data)
        except MalformedRecordError as exc:
            raise ConfigError(f"resolved document: {exc.reason}") from None
        if not isinstance(document, dict):
            raise ConfigError("resolved document: expected an object")
        config = cls(document)
        if config.to_bytes() != data:
            raise ConfigError("resolved document: bytes are not in canonical form")
        return config

    def __contains__(self, key: str) -> bool:
        try:
            self.get(key)
        except ConfigError:
            return False
        return True

    def get(self, key: str) -> object:
        """Return the value at a dotted key."""
        value: object = self.document
        for part in key.split("."):
            if not isinstance(value, Mapping) or part not in value:
                raise ConfigError(f"resolved document has no key {key}")
            value = value[part]
        return value

    def _typed[T](self, key: str, kind: type[T]) -> T:
        value = self.get(key)
        if type(value) is not kind:
            raise ConfigError(f"{key} is not of kind {kind.__name__}")
        return value

    def get_str(self, key: str) -> str:
        return self._typed(key, str)

    def get_int(self, key: str) -> int:
        return self._typed(key, int)

    def get_float(self, key: str) -> float:
        return self._typed(key, float)

    def get_bool(self, key: str) -> bool:
        return self._typed(key, bool)


def resolve(schema: Schema, layers: Sequence[Layer]) -> ResolvedConfig:
    """Apply ``layers`` in order and return the frozen document.

    An undeclared key is an ``UnknownKeyError``; a declared key no layer sets is a
    ``MissingKeyError``. Nothing is defaulted.
    """
    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    for layer in layers:
        setters: dict[str, str] = {}
        for source, table in layer.parts:
            flat = _flatten(schema, source, table)
            for key in flat:
                if key in setters:
                    raise LayerError(
                        layer.name, f"{key} is set in both {setters[key]} and {source}"
                    )
                setters[key] = source
            values.update(flat)
        sources.update(setters)
    missing = tuple(sorted(set(schema.fields) - set(values)))
    if missing:
        raise MissingKeyError(missing)
    document: dict[str, object] = {}
    for key, value in values.items():
        _insert(document, key, value)
    return ResolvedConfig(document, sources)


def resolve_paths(
    schema: Schema, paths: Sequence[Path], overrides: Sequence[str] = ()
) -> ResolvedConfig:
    """Resolve layer files and directories in order, then command-line overrides."""
    layers = [load_layer(path) for path in paths]
    if overrides:
        layers.append(parse_overrides(schema, overrides))
    return resolve(schema, layers)
