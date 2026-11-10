"""The requirement 1 contract types (clauses 1.1 to 1.3, design section 4.1).

``MemoryItem``, ``Query``, ``Candidate``, ``Provenance`` and ``AssembledContext`` are
frozen, validated on construction and carry ``schema_version``. No field belongs to
one domain: ``kind`` is an open vocabulary, ``source`` is an opaque origin and
reference, and ``metadata`` is a free map that no stage may require.

Decoding is strict: an unknown ``schema_version`` raises ``SchemaVersionError``; a
missing required field, wrong type, unknown field, invalid instant or an ``id`` that
does not match its content raises ``MalformedRecordError``. Fields the contract calls
optional (``metadata``, ``filters`` and the nullable members) may be absent on input
and are always written in the canonical form.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import NoReturn, Self

from trina.schema.canonical import (
    ID_RE,
    canonical_bytes,
    content_identity,
    format_instant,
    freeze_map,
    normalise_instant,
    normalise_text,
    parse_instant,
    parse_json,
    thaw_json,
)
from trina.schema.errors import MalformedRecordError, SchemaVersionError

SCHEMA_VERSION = 1
SUPPORTED_VERSIONS: tuple[int, ...] = (1,)

# Proposed core vocabulary; `kind` stays open and a stage must accept an unknown value.
CORE_KINDS: tuple[str, ...] = (
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_output",
    "file_content",
    "note",
    "fact",
    "preference",
    "event",
    "document",
)

# Stage names a degraded context may carry, in pipeline order.
STAGES: tuple[str, ...] = ("retriever", "selector", "assembler")

_EMPTY: Mapping[str, object] = MappingProxyType({})


def _fail(record_type: str, path: str, reason: str) -> NoReturn:
    raise MalformedRecordError(record_type, path, reason)


def _object(
    record_type: str,
    value: object,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> Mapping[str, object]:
    """Check an object's shape: every required field, no unknown field."""
    if not isinstance(value, Mapping):
        _fail(record_type, "", "expected an object")
    for key in value:
        if not isinstance(key, str):
            _fail(record_type, "", "object key is not a string")
    for name in required:
        if name not in value:
            _fail(record_type, name, "missing required field")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        _fail(record_type, unknown[0], "unknown field")
    return value


def _check_version(record_type: str, version: object) -> int:
    if isinstance(version, bool) or not isinstance(version, int):
        _fail(record_type, "schema_version", "expected an integer")
    if version not in SUPPORTED_VERSIONS:
        raise SchemaVersionError(record_type, version, SUPPORTED_VERSIONS)
    return version


def _versioned(record_type: str, value: object) -> Mapping[str, object]:
    """Check the version before anything else, so an unknown version is named as such."""
    if not isinstance(value, Mapping):
        _fail(record_type, "", "expected an object")
    if "schema_version" not in value:
        _fail(record_type, "schema_version", "missing required field")
    _check_version(record_type, value["schema_version"])
    return value


def _name(record_type: str, path: str, value: object) -> str:
    text = normalise_text(record_type, path, value)
    if not text:
        _fail(record_type, path, "must not be empty")
    return text


def _int(record_type: str, path: str, value: object, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(record_type, path, "expected an integer")
    if value < minimum:
        _fail(record_type, path, f"must be at least {minimum}")
    return value


def _number(record_type: str, path: str, value: object, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _fail(record_type, path, "expected a number")
    try:
        number = float(value)
    except OverflowError:
        _fail(record_type, path, "number is not finite")
    if not math.isfinite(number):
        _fail(record_type, path, "number is not finite")
    if minimum is not None and number < minimum:
        _fail(record_type, path, f"must be at least {minimum}")
    return number


def _bool(record_type: str, path: str, value: object) -> bool:
    if not isinstance(value, bool):
        _fail(record_type, path, "expected a boolean")
    return value


def _identity(record_type: str, path: str, value: object) -> str:
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        _fail(record_type, path, "expected a sha256 identity")
    return value


def _array(record_type: str, path: str, value: object) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        _fail(record_type, path, "expected an array")
    return tuple(value)


def _scores(record_type: str, path: str, value: object) -> Mapping[str, float]:
    """Per-stage scores: stage name to finite float. A substituted stage has no entry."""
    if not isinstance(value, Mapping):
        _fail(record_type, path, "expected an object")
    scores: dict[str, float] = {}
    for raw_key, entry in value.items():
        key = _name(record_type, path, raw_key)
        if key in scores:
            _fail(record_type, path, "duplicate key after normalisation")
        scores[key] = _number(record_type, f"{path}.{key}", entry)
    return MappingProxyType(dict(sorted(scores.items())))


def _ranks(record_type: str, path: str, value: object) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        _fail(record_type, path, "expected an object")
    ranks: dict[str, int] = {}
    for raw_key, entry in value.items():
        key = _name(record_type, path, raw_key)
        if key in ranks:
            _fail(record_type, path, "duplicate key after normalisation")
        ranks[key] = _int(record_type, f"{path}.{key}", entry)
    return MappingProxyType(dict(sorted(ranks.items())))


def _optional_instant(record_type: str, path: str, value: object) -> datetime | None:
    return None if value is None else normalise_instant(record_type, path, value)


def _format_optional(value: datetime | None) -> str | None:
    return None if value is None else format_instant(value)


class _Record:
    """Canonical codec shared by the versioned record types."""

    def to_dict(self) -> dict[str, object]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: object) -> Self:
        raise NotImplementedError

    def to_bytes(self) -> bytes:
        """The canonical UTF-8 JSON bytes of this record."""
        return canonical_bytes(self.to_dict())

    def to_json(self) -> str:
        return self.to_bytes().decode("utf-8")

    @classmethod
    def from_json(cls, data: str | bytes) -> Self:
        """Strictly decode JSON text or bytes; the input need not be canonical."""
        return cls.from_dict(parse_json(cls.__name__, data))


@dataclass(frozen=True)
class Source:
    """Where a memory came from: an origin and an opaque reference inside it."""

    origin: str
    ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", _name("Source", "origin", self.origin))
        object.__setattr__(self, "ref", normalise_text("Source", "ref", self.ref))

    def to_dict(self) -> dict[str, object]:
        return {"origin": self.origin, "ref": self.ref}

    @classmethod
    def from_dict(cls, data: object) -> Self:
        fields = _object("Source", data, ("origin", "ref"))
        return cls(
            origin=_name("Source", "origin", fields["origin"]),
            ref=normalise_text("Source", "ref", fields["ref"]),
        )


@dataclass(frozen=True)
class Span:
    """A half-open range ``[start, end)`` in Unicode code points of canonical item text."""

    start: int
    end: int

    def __post_init__(self) -> None:
        _int("Span", "start", self.start)
        _int("Span", "end", self.end, minimum=self.start)

    def to_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, data: object) -> Self:
        fields = _object("Span", data, ("start", "end"))
        start = _int("Span", "start", fields["start"])
        return cls(start=start, end=_int("Span", "end", fields["end"], minimum=start))


def _optional_span(value: object) -> Span | None:
    return None if value is None else Span.from_dict(value)


@dataclass(frozen=True)
class Filters:
    """The optional time and kind filters of a query (clauses 1.2 and 7.4).

    ``None`` means the filter is not applied. ``kinds`` is a set: it is held sorted
    and without duplicates so that equal filters give an equal query identity.
    """

    created_after: datetime | None = None
    created_before: datetime | None = None
    kinds: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        after = _optional_instant("Filters", "created_after", self.created_after)
        before = _optional_instant("Filters", "created_before", self.created_before)
        object.__setattr__(self, "created_after", after)
        object.__setattr__(self, "created_before", before)
        if self.kinds is not None:
            raw: object = self.kinds
            if not isinstance(raw, list | tuple | set | frozenset):
                _fail("Filters", "kinds", "expected an array")
            kinds = {_name("Filters", "kinds", kind) for kind in raw}
            if not kinds:
                _fail("Filters", "kinds", "must not be empty; omit it to apply no kind filter")
            object.__setattr__(self, "kinds", tuple(sorted(kinds)))

    def to_dict(self) -> dict[str, object]:
        return {
            "created_after": _format_optional(self.created_after),
            "created_before": _format_optional(self.created_before),
            "kinds": None if self.kinds is None else list(self.kinds),
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        fields = _object(
            "Filters", data, (), optional=("created_after", "created_before", "kinds")
        )
        after = fields.get("created_after")
        before = fields.get("created_before")
        kinds = fields.get("kinds")
        return cls(
            created_after=(
                None if after is None else parse_instant("Filters", "created_after", after)
            ),
            created_before=(
                None if before is None else parse_instant("Filters", "created_before", before)
            ),
            kinds=(
                None
                if kinds is None
                else tuple(
                    _name("Filters", "kinds", kind) for kind in _array("Filters", "kinds", kinds)
                )
            ),
        )


@dataclass(frozen=True)
class MemoryItem(_Record):
    """One memory (clause 1.1). ``id`` is derived from every field except ``metadata``."""

    id: str
    scope: str
    kind: str
    text: str
    created_at: datetime
    source: Source
    metadata: Mapping[str, object] = field(default=_EMPTY, hash=False)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        rt = "MemoryItem"
        _check_version(rt, self.schema_version)
        object.__setattr__(self, "scope", _name(rt, "scope", self.scope))
        object.__setattr__(self, "kind", _name(rt, "kind", self.kind))
        object.__setattr__(self, "text", normalise_text(rt, "text", self.text))
        created_at = normalise_instant(rt, "created_at", self.created_at)
        object.__setattr__(self, "created_at", created_at)
        if not isinstance(self.source, Source):
            _fail(rt, "source", "expected a Source")
        object.__setattr__(self, "metadata", freeze_map(rt, "metadata", self.metadata))
        _identity(rt, "id", self.id)
        if self.id != content_identity("memory-item", self.schema_version, self._content()):
            _fail(rt, "id", "identity does not match content")

    def _content(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "kind": self.kind,
            "text": self.text,
            "created_at": format_instant(self.created_at),
            "source": self.source.to_dict(),
        }

    @classmethod
    def create(
        cls,
        *,
        scope: str,
        kind: str,
        text: str,
        created_at: datetime,
        source: Source,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        """The one constructor for every domain: derives ``id`` from the content."""
        rt = "MemoryItem"
        if not isinstance(source, Source):
            _fail(rt, "source", "expected a Source")
        content: dict[str, object] = {
            "scope": _name(rt, "scope", scope),
            "kind": _name(rt, "kind", kind),
            "text": normalise_text(rt, "text", text),
            "created_at": format_instant(normalise_instant(rt, "created_at", created_at)),
            "source": source.to_dict(),
        }
        return cls(
            id=content_identity("memory-item", SCHEMA_VERSION, content),
            scope=scope,
            kind=kind,
            text=text,
            created_at=created_at,
            source=source,
            metadata=_EMPTY if metadata is None else metadata,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            **self._content(),
            "metadata": thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        rt = "MemoryItem"
        fields = _object(
            rt,
            _versioned(rt, data),
            ("schema_version", "id", "scope", "kind", "text", "created_at", "source"),
            optional=("metadata",),
        )
        return cls(
            id=_identity(rt, "id", fields["id"]),
            scope=_name(rt, "scope", fields["scope"]),
            kind=_name(rt, "kind", fields["kind"]),
            text=normalise_text(rt, "text", fields["text"]),
            created_at=parse_instant(rt, "created_at", fields["created_at"]),
            source=Source.from_dict(fields["source"]),
            metadata=freeze_map(rt, "metadata", fields.get("metadata", _EMPTY)),
            schema_version=_check_version(rt, fields["schema_version"]),
        )


@dataclass(frozen=True)
class Query(_Record):
    """A retrieval request (clause 1.2). ``id`` excludes ``metadata``.

    ``reference_time`` is the instant the query is asked; items created after it are
    never candidates. ``token_budget`` is counted by the declared tokenizer.
    """

    id: str
    text: str
    scope: str
    reference_time: datetime
    token_budget: int
    filters: Filters = field(default_factory=Filters)
    metadata: Mapping[str, object] = field(default=_EMPTY, hash=False)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        rt = "Query"
        _check_version(rt, self.schema_version)
        object.__setattr__(self, "text", normalise_text(rt, "text", self.text))
        object.__setattr__(self, "scope", _name(rt, "scope", self.scope))
        reference_time = normalise_instant(rt, "reference_time", self.reference_time)
        object.__setattr__(self, "reference_time", reference_time)
        _int(rt, "token_budget", self.token_budget)
        if not isinstance(self.filters, Filters):
            _fail(rt, "filters", "expected Filters")
        object.__setattr__(self, "metadata", freeze_map(rt, "metadata", self.metadata))
        _identity(rt, "id", self.id)
        if self.id != content_identity("query", self.schema_version, self._content()):
            _fail(rt, "id", "identity does not match content")

    def _content(self) -> dict[str, object]:
        return {
            "text": self.text,
            "scope": self.scope,
            "reference_time": format_instant(self.reference_time),
            "token_budget": self.token_budget,
            "filters": self.filters.to_dict(),
        }

    @classmethod
    def create(
        cls,
        *,
        text: str,
        scope: str,
        reference_time: datetime,
        token_budget: int,
        filters: Filters | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Self:
        """Build a query and derive its ``id`` from the content."""
        rt = "Query"
        chosen = Filters() if filters is None else filters
        if not isinstance(chosen, Filters):
            _fail(rt, "filters", "expected Filters")
        content: dict[str, object] = {
            "text": normalise_text(rt, "text", text),
            "scope": _name(rt, "scope", scope),
            "reference_time": format_instant(
                normalise_instant(rt, "reference_time", reference_time)
            ),
            "token_budget": _int(rt, "token_budget", token_budget),
            "filters": chosen.to_dict(),
        }
        return cls(
            id=content_identity("query", SCHEMA_VERSION, content),
            text=text,
            scope=scope,
            reference_time=reference_time,
            token_budget=token_budget,
            filters=chosen,
            metadata=_EMPTY if metadata is None else metadata,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            **self._content(),
            "metadata": thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        rt = "Query"
        fields = _object(
            rt,
            _versioned(rt, data),
            ("schema_version", "id", "text", "scope", "reference_time", "token_budget"),
            optional=("filters", "metadata"),
        )
        filters = fields.get("filters")
        return cls(
            id=_identity(rt, "id", fields["id"]),
            text=normalise_text(rt, "text", fields["text"]),
            scope=_name(rt, "scope", fields["scope"]),
            reference_time=parse_instant(rt, "reference_time", fields["reference_time"]),
            token_budget=_int(rt, "token_budget", fields["token_budget"]),
            filters=Filters() if filters is None else Filters.from_dict(filters),
            metadata=freeze_map(rt, "metadata", fields.get("metadata", _EMPTY)),
            schema_version=_check_version(rt, fields["schema_version"]),
        )


@dataclass(frozen=True)
class Features:
    """Structured signals of a candidate; none is specific to a domain."""

    token_count: int
    recency_seconds: float
    rank: Mapping[str, int] = field(default=MappingProxyType({}), hash=False)
    matched_span: Span | None = None

    def __post_init__(self) -> None:
        rt = "Features"
        _int(rt, "token_count", self.token_count)
        recency = _number(rt, "recency_seconds", self.recency_seconds, minimum=0.0)
        object.__setattr__(self, "recency_seconds", recency)
        object.__setattr__(self, "rank", _ranks(rt, "rank", self.rank))
        if self.matched_span is not None and not isinstance(self.matched_span, Span):
            _fail(rt, "matched_span", "expected a Span")

    def to_dict(self) -> dict[str, object]:
        return {
            "token_count": self.token_count,
            "recency_seconds": self.recency_seconds,
            "rank": dict(self.rank),
            "matched_span": None if self.matched_span is None else self.matched_span.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        rt = "Features"
        fields = _object(
            rt, data, ("token_count", "recency_seconds"), optional=("rank", "matched_span")
        )
        return cls(
            token_count=_int(rt, "token_count", fields["token_count"]),
            recency_seconds=_number(rt, "recency_seconds", fields["recency_seconds"], minimum=0.0),
            rank=_ranks(rt, "rank", fields.get("rank", _EMPTY)),
            matched_span=_optional_span(fields.get("matched_span")),
        )


@dataclass(frozen=True)
class Candidate(_Record):
    """A memory proposed for a query, with the scores and features stages attach."""

    item: MemoryItem
    features: Features
    scores: Mapping[str, float] = field(default=MappingProxyType({}), hash=False)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        rt = "Candidate"
        _check_version(rt, self.schema_version)
        if not isinstance(self.item, MemoryItem):
            _fail(rt, "item", "expected a MemoryItem")
        if not isinstance(self.features, Features):
            _fail(rt, "features", "expected Features")
        object.__setattr__(self, "scores", _scores(rt, "scores", self.scores))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "item": self.item.to_dict(),
            "scores": dict(self.scores),
            "features": self.features.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        rt = "Candidate"
        fields = _object(
            rt,
            _versioned(rt, data),
            ("schema_version", "item", "features"),
            optional=("scores",),
        )
        return cls(
            item=MemoryItem.from_dict(fields["item"]),
            features=Features.from_dict(fields["features"]),
            scores=_scores(rt, "scores", fields.get("scores", _EMPTY)),
            schema_version=_check_version(rt, fields["schema_version"]),
        )


@dataclass(frozen=True)
class Provenance(_Record):
    """Why and how one item is in a context (clauses 1.2 and 10.3).

    Names the source item, each stage score that led to inclusion, the place in the
    context, the cost under the declared tokenizer, and any truncation with the
    source span the emitted text maps to. A truncated item always has its span.
    """

    item_id: str
    source: Source
    position: int
    token_count: int
    scores: Mapping[str, float] = field(default=MappingProxyType({}), hash=False)
    truncated: bool = False
    source_span: Span | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        rt = "Provenance"
        _check_version(rt, self.schema_version)
        _identity(rt, "item_id", self.item_id)
        if not isinstance(self.source, Source):
            _fail(rt, "source", "expected a Source")
        _int(rt, "position", self.position)
        _int(rt, "token_count", self.token_count)
        object.__setattr__(self, "scores", _scores(rt, "scores", self.scores))
        _bool(rt, "truncated", self.truncated)
        if self.source_span is not None and not isinstance(self.source_span, Span):
            _fail(rt, "source_span", "expected a Span")
        if self.truncated and self.source_span is None:
            _fail(rt, "source_span", "a truncated item must name its source span")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "item_id": self.item_id,
            "source": self.source.to_dict(),
            "scores": dict(self.scores),
            "position": self.position,
            "token_count": self.token_count,
            "truncated": self.truncated,
            "source_span": None if self.source_span is None else self.source_span.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        rt = "Provenance"
        fields = _object(
            rt,
            _versioned(rt, data),
            (
                "schema_version",
                "item_id",
                "source",
                "scores",
                "position",
                "token_count",
                "truncated",
            ),
            optional=("source_span",),
        )
        return cls(
            item_id=_identity(rt, "item_id", fields["item_id"]),
            source=Source.from_dict(fields["source"]),
            position=_int(rt, "position", fields["position"]),
            token_count=_int(rt, "token_count", fields["token_count"]),
            scores=_scores(rt, "scores", fields["scores"]),
            truncated=_bool(rt, "truncated", fields["truncated"]),
            source_span=_optional_span(fields.get("source_span")),
            schema_version=_check_version(rt, fields["schema_version"]),
        )


@dataclass(frozen=True)
class ContextItem:
    """One entry of an assembled context: the emitted text with its provenance."""

    text: str
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", normalise_text("ContextItem", "text", self.text))
        if not isinstance(self.provenance, Provenance):
            _fail("ContextItem", "provenance", "expected a Provenance")

    def to_dict(self) -> dict[str, object]:
        return {"text": self.text, "provenance": self.provenance.to_dict()}

    @classmethod
    def from_dict(cls, data: object) -> Self:
        fields = _object("ContextItem", data, ("text", "provenance"))
        return cls(
            text=normalise_text("ContextItem", "text", fields["text"]),
            provenance=Provenance.from_dict(fields["provenance"]),
        )


def _stages(record_type: str, path: str, value: Iterable[object]) -> tuple[str, ...]:
    """Validate degraded stage names and hold them in pipeline order."""
    names: set[str] = set()
    for entry in value:
        if not isinstance(entry, str) or entry not in STAGES:
            _fail(record_type, path, "unknown stage name")
        if entry in names:
            _fail(record_type, path, "duplicate stage name")
        names.add(entry)
    return tuple(stage for stage in STAGES if stage in names)


@dataclass(frozen=True)
class AssembledContext(_Record):
    """The response to a query (clause 1.2).

    ``token_count`` is the count of the rendered context under ``tokenizer`` and never
    exceeds ``token_budget``. ``abstained`` is true when the selector returned no
    evidence, and ``items`` is then empty. ``degraded`` names each stage that fell
    back to its baseline; it is empty on the full learned path.
    """

    query_id: str
    items: tuple[ContextItem, ...]
    token_count: int
    token_budget: int
    tokenizer: str
    abstained: bool
    degraded: tuple[str, ...]
    bundle_version: str
    ordering_policy: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        rt = "AssembledContext"
        _check_version(rt, self.schema_version)
        _identity(rt, "query_id", self.query_id)
        items = _array(rt, "items", self.items)
        for index, entry in enumerate(items):
            if not isinstance(entry, ContextItem):
                _fail(rt, f"items[{index}]", "expected a ContextItem")
            if entry.provenance.position != index:
                _fail(rt, f"items[{index}].provenance.position", "does not match item order")
        object.__setattr__(self, "items", items)
        _int(rt, "token_count", self.token_count)
        _int(rt, "token_budget", self.token_budget)
        if self.token_count > self.token_budget:
            _fail(rt, "token_count", "exceeds token_budget")
        object.__setattr__(self, "tokenizer", _name(rt, "tokenizer", self.tokenizer))
        _bool(rt, "abstained", self.abstained)
        if self.abstained and items:
            _fail(rt, "items", "an abstaining context carries no items")
        degraded = _stages(rt, "degraded", _array(rt, "degraded", self.degraded))
        object.__setattr__(self, "degraded", degraded)
        bundle_version = _name(rt, "bundle_version", self.bundle_version)
        object.__setattr__(self, "bundle_version", bundle_version)
        ordering_policy = _name(rt, "ordering_policy", self.ordering_policy)
        object.__setattr__(self, "ordering_policy", ordering_policy)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "query_id": self.query_id,
            "items": [entry.to_dict() for entry in self.items],
            "token_count": self.token_count,
            "token_budget": self.token_budget,
            "tokenizer": self.tokenizer,
            "abstained": self.abstained,
            "degraded": list(self.degraded),
            "bundle_version": self.bundle_version,
            "ordering_policy": self.ordering_policy,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        rt = "AssembledContext"
        fields = _object(
            rt,
            _versioned(rt, data),
            (
                "schema_version",
                "query_id",
                "items",
                "token_count",
                "token_budget",
                "tokenizer",
                "abstained",
                "degraded",
                "bundle_version",
                "ordering_policy",
            ),
        )
        return cls(
            query_id=_identity(rt, "query_id", fields["query_id"]),
            items=tuple(
                ContextItem.from_dict(entry) for entry in _array(rt, "items", fields["items"])
            ),
            token_count=_int(rt, "token_count", fields["token_count"]),
            token_budget=_int(rt, "token_budget", fields["token_budget"]),
            tokenizer=_name(rt, "tokenizer", fields["tokenizer"]),
            abstained=_bool(rt, "abstained", fields["abstained"]),
            degraded=_stages(rt, "degraded", _array(rt, "degraded", fields["degraded"])),
            bundle_version=_name(rt, "bundle_version", fields["bundle_version"]),
            ordering_policy=_name(rt, "ordering_policy", fields["ordering_policy"]),
            schema_version=_check_version(rt, fields["schema_version"]),
        )
