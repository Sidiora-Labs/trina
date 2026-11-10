"""Task 0.2: the memory, query and assembled-context contract (case TRM-001).

Golden vectors under tests/fixtures/schema/golden fix the canonical bytes of every
contract type; the records are rebuilt here from literals and must reproduce them
byte for byte. Identities are recomputed from a hand-written preimage and in fresh
interpreters with other hash seeds, time zones and locales. Rejection cases cover
unknown versions and each malformed shape. The domain-agnostic property builds
items from a coding episode and a conversational session through one constructor.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import os
import random
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from trina.schema import (
    CORE_KINDS,
    SCHEMA_VERSION,
    AssembledContext,
    Candidate,
    ContextItem,
    ContractError,
    Features,
    Filters,
    MalformedRecordError,
    MemoryItem,
    Provenance,
    Query,
    SchemaVersionError,
    Source,
    Span,
    StandInTokenizer,
    Tokenizer,
    TokenizerError,
    count_tokens,
    default_tokenizer,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "schema"
GOLDEN = FIXTURES / "golden"
SRC = REPO / "src" / "trina"

RecordType = type[MemoryItem] | type[Query] | type[Candidate] | type[Provenance] | type[
    AssembledContext
]
Record = MemoryItem | Query | Candidate | Provenance | AssembledContext

CET = timezone(timedelta(hours=1))
TRUNCATION_MARK = " […]"


# --------------------------------------------------------------------------- builders


def golden_item() -> MemoryItem:
    # The text is written decomposed (e + U+0301) and the instant with an offset:
    # the canonical form holds NFC text and a UTC instant.
    return MemoryItem.create(
        scope="tenant-a/session-1",
        kind="note",
        text="Cafe\u0301 Zoë opens at 09:00 ☕",
        created_at=datetime(2026, 1, 5, 18, 30, 0, 123456, tzinfo=CET),
        source=Source(origin="synth", ref="persona-7/session-3/turn-2"),
        metadata={"domain": "conversation", "tags": ["β", "a"], "weight": 0.5},
    )


def golden_query() -> Query:
    return Query.create(
        text="When does the café open?",
        scope="tenant-a/session-1",
        reference_time=datetime(2026, 1, 6, 8, 0, 0, tzinfo=UTC),
        token_budget=256,
        filters=Filters(
            created_after=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            kinds=("note", "fact"),
        ),
        metadata={"category": "single_hop"},
    )


def golden_candidate() -> Candidate:
    item, query, tokenizer = golden_item(), golden_query(), default_tokenizer()
    return Candidate(
        item=item,
        features=Features(
            token_count=count_tokens(item.text, tokenizer),
            recency_seconds=(query.reference_time - item.created_at).total_seconds(),
            rank={"dense": 3, "bm25": 1, "fused": 0},
            matched_span=Span(0, 8),
        ),
        scores={"bm25": 12.5, "dense": 0.1, "fused": 1e-07, "selector": 0.9731, "utility": 3},
    )


def golden_provenance() -> Provenance:
    item, tokenizer = golden_item(), default_tokenizer()
    return Provenance(
        item_id=item.id,
        source=item.source,
        position=0,
        token_count=count_tokens(item.text[:8] + TRUNCATION_MARK, tokenizer),
        scores={"fused": 1e-07, "reranker": -2.25},
        truncated=True,
        source_span=Span(0, 8),
    )


def golden_context() -> AssembledContext:
    item, query, tokenizer = golden_item(), golden_query(), default_tokenizer()
    entry = ContextItem(text=item.text[:8] + TRUNCATION_MARK, provenance=golden_provenance())
    return AssembledContext(
        query_id=query.id,
        items=(entry,),
        token_count=count_tokens(entry.text, tokenizer),
        token_budget=query.token_budget,
        tokenizer=tokenizer.id,
        abstained=False,
        degraded=("selector",),
        bundle_version="bundle-0.0.1",
        ordering_policy="chronological",
    )


GOLDEN_CASES: dict[str, tuple[RecordType, Callable[[], Record]]] = {
    "memory_item": (MemoryItem, golden_item),
    "query": (Query, golden_query),
    "candidate": (Candidate, golden_candidate),
    "provenance": (Provenance, golden_provenance),
    "assembled_context": (AssembledContext, golden_context),
}


def golden_bytes(name: str) -> bytes:
    return (GOLDEN / f"{name}.json").read_bytes()


def golden_dict(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(golden_bytes(name))
    return loaded


def all_strings_are_nfc(value: object) -> bool:
    """Every key and string value is NFC. (JSON escapes mean the whole text need not be.)"""
    if isinstance(value, str):
        return unicodedata.is_normalized("NFC", value)
    if isinstance(value, dict):
        return all(all_strings_are_nfc(k) and all_strings_are_nfc(v) for k, v in value.items())
    if isinstance(value, list):
        return all(all_strings_are_nfc(entry) for entry in value)
    return True


# --------------------------------------------------------- golden vectors, round trips


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_golden_vector_reproduces_byte_for_byte(name: str) -> None:
    record_type, build = GOLDEN_CASES[name]
    expected = golden_bytes(name)
    record = build()
    assert record.to_bytes() == expected
    decoded = record_type.from_json(expected)
    assert decoded == record
    assert decoded.to_bytes() == expected
    assert record_type.from_json(expected.decode("utf-8")).to_bytes() == expected
    assert record_type.from_dict(record.to_dict()) == record


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_golden_bytes_are_canonical(name: str) -> None:
    raw = golden_bytes(name)
    text = raw.decode("utf-8")
    assert all_strings_are_nfc(json.loads(text))
    # Sorted keys by code point, no insignificant whitespace, non-ASCII written raw.
    reencoded = json.dumps(
        json.loads(text), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert reencoded.encode("utf-8") == raw
    assert not raw.endswith(b"\n")
    assert golden_dict(name)["schema_version"] == SCHEMA_VERSION == 1


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_decoding_accepts_non_canonical_spelling_and_writes_canonical(name: str) -> None:
    record_type, _ = GOLDEN_CASES[name]
    loaded = golden_dict(name)
    shuffled = dict(reversed(list(loaded.items())))
    pretty = json.dumps(shuffled, indent=2, ensure_ascii=True)
    assert record_type.from_json(pretty).to_bytes() == golden_bytes(name)


def test_golden_content_is_what_the_contract_names() -> None:
    item = golden_dict("memory_item")
    assert set(item) == {
        "schema_version",
        "id",
        "scope",
        "kind",
        "text",
        "created_at",
        "source",
        "metadata",
    }
    assert item["text"] == "Caf\u00e9 Zoë opens at 09:00 ☕"
    assert item["created_at"] == "2026-01-05T17:30:00.123456Z"
    assert item["source"] == {"origin": "synth", "ref": "persona-7/session-3/turn-2"}

    query = golden_dict("query")
    assert set(query) == {
        "schema_version",
        "id",
        "text",
        "scope",
        "reference_time",
        "token_budget",
        "filters",
        "metadata",
    }
    assert query["filters"] == {
        "created_after": "2026-01-01T00:00:00.000000Z",
        "created_before": None,
        "kinds": ["fact", "note"],
    }

    context = golden_dict("assembled_context")
    assert set(context) == {
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
    }
    assert context["query_id"] == query["id"]
    assert context["abstained"] is False
    assert context["degraded"] == ["selector"]
    (entry,) = context["items"]
    provenance = entry["provenance"]
    assert provenance["item_id"] == item["id"]
    assert provenance["source"] == item["source"]
    assert provenance["scores"] == {"fused": 1e-07, "reranker": -2.25}
    assert provenance["truncated"] is True
    assert provenance["source_span"] == {"start": 0, "end": 8}
    span = provenance["source_span"]
    assert entry["text"] == item["text"][span["start"] : span["end"]] + TRUNCATION_MARK


def test_scores_are_written_in_shortest_round_trip_form() -> None:
    raw = golden_bytes("candidate")
    assert (
        b'"scores":{"bm25":12.5,"dense":0.1,"fused":1e-07,"selector":0.9731,"utility":3.0}' in raw
    )
    assert b'"recency_seconds":52199.876544' in raw
    candidate = Candidate.from_json(raw)
    assert candidate.scores["dense"] == 0.1
    assert isinstance(candidate.scores["utility"], float)


# ------------------------------------------------------------------ identity stability

# Hand-written preimages: prefix, then canonical JSON of the identity fields.
ITEM_PREIMAGE = (
    "trina\0memory-item\0v1\0"
    '{"created_at":"2026-01-05T17:30:00.123456Z","kind":"note",'
    '"scope":"tenant-a/session-1",'
    '"source":{"origin":"synth","ref":"persona-7/session-3/turn-2"},'
    '"text":"Caf\u00e9 Zoë opens at 09:00 ☕"}'
)
QUERY_PREIMAGE = (
    "trina\0query\0v1\0"
    '{"filters":{"created_after":"2026-01-01T00:00:00.000000Z","created_before":null,'
    '"kinds":["fact","note"]},"reference_time":"2026-01-06T08:00:00.000000Z",'
    '"scope":"tenant-a/session-1","text":"When does the caf\u00e9 open?","token_budget":256}'
)


def test_identity_is_sha256_of_declared_preimage() -> None:
    item_id = "sha256:" + hashlib.sha256(ITEM_PREIMAGE.encode("utf-8")).hexdigest()
    query_id = "sha256:" + hashlib.sha256(QUERY_PREIMAGE.encode("utf-8")).hexdigest()
    assert golden_item().id == item_id == golden_dict("memory_item")["id"]
    assert golden_query().id == query_id == golden_dict("query")["id"]


def test_identical_content_gives_identical_identity() -> None:
    base = golden_item()
    same = MemoryItem.create(
        scope="tenant-a/session-1",
        kind="note",
        text="Caf\u00e9 Zoë opens at 09:00 ☕",  # composed spelling of the same text
        created_at=datetime(2026, 1, 5, 17, 30, 0, 123456, tzinfo=UTC),  # same instant
        source=Source(origin="synth", ref="persona-7/session-3/turn-2"),
        metadata={"anything": {"else": [1, 2, 3]}},  # metadata is excluded from id
    )
    assert same.id == base.id
    assert same != base  # metadata still distinguishes the records


def test_every_identity_field_changes_the_identity() -> None:
    base: dict[str, Any] = {
        "scope": "s",
        "kind": "note",
        "text": "t",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "source": Source("synth", "r"),
    }
    changes: dict[str, Any] = {
        "scope": "s2",
        "kind": "fact",
        "text": "t2",
        "created_at": datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
        "source": Source("synth", "r2"),
    }
    seen = {MemoryItem.create(**base).id}
    for name, value in changes.items():
        seen.add(MemoryItem.create(**{**base, name: value}).id)
    seen.add(MemoryItem.create(**{**base, "source": Source("bucket", "r")}).id)
    assert len(seen) == 7


def test_query_identity_covers_budget_and_filters_but_not_metadata() -> None:
    def build(**overrides: Any) -> Query:
        arguments: dict[str, Any] = {
            "text": "q",
            "scope": "s",
            "reference_time": datetime(2026, 1, 1, tzinfo=UTC),
            "token_budget": 100,
        }
        arguments.update(overrides)
        return Query.create(**arguments)

    base = build()
    assert build(metadata={"domain": "coding"}).id == base.id
    assert build(filters=Filters()).id == base.id
    assert build(token_budget=101).id != base.id
    assert build(text="q2").id != base.id
    assert build(scope="s2").id != base.id
    assert build(reference_time=datetime(2026, 1, 2, tzinfo=UTC)).id != base.id
    with_kinds = build(filters=Filters(kinds=("note", "fact")))
    assert with_kinds.id != base.id
    # `kinds` is a set: order and repetition do not change the identity.
    assert build(filters=Filters(kinds=("fact", "note", "fact"))).id == with_kinds.id
    assert build(filters=Filters(created_before=datetime(2026, 1, 1, tzinfo=UTC))).id != base.id
    # A query and an item with the same text never share an identity.
    assert base.id != MemoryItem.create(
        scope="s", kind="q", text="q", created_at=base.reference_time, source=Source("o", "")
    ).id


STABILITY_SCRIPT = """
import hashlib, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from trina.schema import AssembledContext, Candidate, MemoryItem, Provenance, Query, Source

golden = Path(sys.argv[1])
item = MemoryItem.create(
    scope="tenant-a/session-1",
    kind="note",
    text="Cafe\\u0301 Zoë opens at 09:00 ☕",
    created_at=datetime(2026, 1, 5, 18, 30, 0, 123456, tzinfo=timezone(timedelta(hours=1))),
    source=Source(origin="synth", ref="persona-7/session-3/turn-2"),
    metadata={"weight": 0.5, "tags": ["β", "a"], "domain": "conversation"},
)
print(item.id)
print(hashlib.sha256(item.to_bytes()).hexdigest())
types = {
    "memory_item": MemoryItem,
    "query": Query,
    "candidate": Candidate,
    "provenance": Provenance,
    "assembled_context": AssembledContext,
}
for name, record_type in types.items():
    record = record_type.from_json((golden / f"{name}.json").read_bytes())
    print(name, hashlib.sha256(record.to_bytes()).hexdigest())
"""


@pytest.mark.parametrize(
    "overrides",
    [
        {"PYTHONHASHSEED": "0"},
        {"PYTHONHASHSEED": "987654321", "TZ": "Asia/Kolkata"},
        {"PYTHONHASHSEED": "random", "TZ": "America/St_Johns", "LC_ALL": "C"},
    ],
)
def test_identity_and_bytes_are_stable_across_interpreters(overrides: dict[str, str]) -> None:
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", **overrides}
    result = subprocess.run(
        [sys.executable, "-c", STABILITY_SCRIPT, str(GOLDEN)],
        capture_output=True,
        encoding="utf-8",
        env=environment,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines[0] == golden_dict("memory_item")["id"]
    assert lines[1] == hashlib.sha256(golden_bytes("memory_item")).hexdigest()
    expected = [f"{name} {hashlib.sha256(golden_bytes(name)).hexdigest()}" for name in GOLDEN_CASES]
    assert lines[2:] == expected


# --------------------------------------------------------------------- rejection cases


def test_error_types_form_one_hierarchy() -> None:
    assert issubclass(SchemaVersionError, ContractError)
    assert issubclass(MalformedRecordError, ContractError)
    assert issubclass(TokenizerError, ContractError)
    assert not issubclass(SchemaVersionError, MalformedRecordError)
    assert not issubclass(MalformedRecordError, SchemaVersionError)


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
@pytest.mark.parametrize("version", [0, 2, 99, -1])
def test_unknown_version_is_rejected_with_its_typed_error(name: str, version: int) -> None:
    record_type, _ = GOLDEN_CASES[name]
    record = golden_dict(name)
    record["schema_version"] = version
    # The version is judged first: the record is also malformed in other ways.
    record["field_from_the_future"] = True
    with pytest.raises(SchemaVersionError) as caught:
        record_type.from_dict(record)
    assert caught.value.found == version
    assert caught.value.supported == (1,)
    with pytest.raises(SchemaVersionError):
        record_type.from_json(json.dumps(record))


def test_unknown_version_of_a_nested_record_is_rejected() -> None:
    candidate = golden_dict("candidate")
    candidate["item"]["schema_version"] = 2
    with pytest.raises(SchemaVersionError):
        Candidate.from_dict(candidate)
    context = golden_dict("assembled_context")
    context["items"][0]["provenance"]["schema_version"] = 2
    with pytest.raises(SchemaVersionError):
        AssembledContext.from_dict(context)


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
@pytest.mark.parametrize("version", ["1", 1.0, True, None])
def test_version_of_the_wrong_type_is_malformed(name: str, version: object) -> None:
    record_type, _ = GOLDEN_CASES[name]
    record = golden_dict(name)
    record["schema_version"] = version
    with pytest.raises(MalformedRecordError):
        record_type.from_dict(record)


OPTIONAL_FIELDS = {
    "memory_item": {"metadata"},
    "query": {"filters", "metadata"},
    "candidate": {"scores"},
    "provenance": {"source_span"},
    "assembled_context": set[str](),
}


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_each_missing_required_field_is_malformed(name: str) -> None:
    record_type, _ = GOLDEN_CASES[name]
    complete = golden_dict(name)
    required = set(complete) - OPTIONAL_FIELDS[name]
    assert required
    for field_name in sorted(required):
        record = copy.deepcopy(complete)
        del record[field_name]
        with pytest.raises(MalformedRecordError) as caught:
            record_type.from_dict(record)
        assert caught.value.path == field_name
        assert not isinstance(caught.value, SchemaVersionError)


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_unknown_top_level_field_is_malformed(name: str) -> None:
    record_type, _ = GOLDEN_CASES[name]
    record = golden_dict(name)
    record["repository"] = "acme/widgets"
    with pytest.raises(MalformedRecordError) as caught:
        record_type.from_dict(record)
    assert caught.value.path == "repository"


Mutation = Callable[[dict[str, Any]], None]


def _set(path: str, value: object) -> Mutation:
    def mutate(record: dict[str, Any]) -> None:
        target: Any = record
        *parents, last = path.split("/")
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        target[int(last) if last.isdigit() else last] = value

    return mutate


MALFORMED: list[tuple[str, str, Mutation]] = [
    # wrong types
    ("memory_item", "scope is a number", _set("scope", 7)),
    ("memory_item", "scope is empty", _set("scope", "")),
    ("memory_item", "kind is null", _set("kind", None)),
    ("memory_item", "kind is empty", _set("kind", "")),
    ("memory_item", "text is a list", _set("text", ["a"])),
    ("memory_item", "source is a string", _set("source", "synth")),
    ("memory_item", "source lacks ref", _set("source", {"origin": "synth"})),
    ("memory_item", "source has unknown field", _set("source/session", "s")),
    ("memory_item", "source origin is empty", _set("source/origin", "")),
    ("memory_item", "metadata is a list", _set("metadata", [])),
    ("memory_item", "metadata is null", _set("metadata", None)),
    # invalid instants
    ("memory_item", "instant without fraction", _set("created_at", "2026-01-05T17:30:00Z")),
    ("memory_item", "instant with offset", _set("created_at", "2026-01-05T18:30:00.123456+01:00")),
    ("memory_item", "instant with 3 digits", _set("created_at", "2026-01-05T17:30:00.123Z")),
    ("memory_item", "instant lower case", _set("created_at", "2026-01-05t17:30:00.123456z")),
    ("memory_item", "instant not a date", _set("created_at", "2026-02-30T17:30:00.123456Z")),
    ("memory_item", "instant hour 24", _set("created_at", "2026-01-05T24:00:00.000000Z")),
    ("memory_item", "instant is epoch number", _set("created_at", 1767634200)),
    ("memory_item", "instant then newline", _set("created_at", "2026-01-05T17:30:00.123456Z\n")),
    # identity
    ("memory_item", "id does not match content", _set("text", "another text")),
    ("memory_item", "id of other content", _set("id", "sha256:" + "0" * 64)),
    ("memory_item", "id upper case", _set("id", "sha256:" + "A" * 64)),
    ("memory_item", "id without prefix", _set("id", "0" * 64)),
    ("memory_item", "id is a number", _set("id", 5)),
    ("query", "id does not match content", _set("token_budget", 257)),
    ("query", "id does not match filters", _set("filters/kinds", ["fact"])),
    # query
    ("query", "scope missing value", _set("scope", "")),
    ("query", "budget negative", _set("token_budget", -1)),
    ("query", "budget is a float", _set("token_budget", 256.0)),
    ("query", "budget is a string", _set("token_budget", "256")),
    ("query", "budget is a boolean", _set("token_budget", True)),
    ("query", "reference_time invalid", _set("reference_time", "yesterday")),
    ("query", "filters is a list", _set("filters", [])),
    ("query", "filters unknown field", _set("filters/repository", "acme")),
    ("query", "filters kinds empty", _set("filters/kinds", [])),
    ("query", "filters kinds a string", _set("filters/kinds", "note")),
    ("query", "filters kinds holds a number", _set("filters/kinds", ["note", 3])),
    ("query", "filters instant invalid", _set("filters/created_after", "2026-01-01")),
    # candidate
    ("candidate", "item is null", _set("item", None)),
    ("candidate", "item is malformed", _set("item/scope", "")),
    ("candidate", "item id mismatch", _set("item/kind", "fact")),
    ("candidate", "score is a string", _set("scores/bm25", "12.5")),
    ("candidate", "score is a boolean", _set("scores/bm25", True)),
    ("candidate", "score is null", _set("scores/bm25", None)),
    ("candidate", "scores is a list", _set("scores", [0.1])),
    ("candidate", "features missing token_count", _set("features", {"recency_seconds": 1.0})),
    ("candidate", "features unknown field", _set("features/file_path", "a.py")),
    ("candidate", "token_count negative", _set("features/token_count", -1)),
    ("candidate", "recency negative", _set("features/recency_seconds", -0.5)),
    ("candidate", "rank is a float", _set("features/rank/bm25", 1.5)),
    ("candidate", "rank negative", _set("features/rank/bm25", -1)),
    ("candidate", "span end before start", _set("features/matched_span", {"start": 5, "end": 4})),
    ("candidate", "span negative", _set("features/matched_span", {"start": -1, "end": 4})),
    ("candidate", "span as pair", _set("features/matched_span", [0, 8])),
    # provenance
    ("provenance", "item_id not an identity", _set("item_id", "item-1")),
    ("provenance", "source is null", _set("source", None)),
    ("provenance", "position negative", _set("position", -1)),
    ("provenance", "token_count is a float", _set("token_count", 3.0)),
    ("provenance", "truncated is a number", _set("truncated", 1)),
    ("provenance", "truncated without span", _set("source_span", None)),
    ("provenance", "span unknown field", _set("source_span/unit", "bytes")),
    ("provenance", "score is a string", _set("scores/fused", "high")),
    # assembled context
    ("assembled_context", "items is an object", _set("items", {})),
    ("assembled_context", "item lacks provenance", _set("items/0", {"text": "x"})),
    ("assembled_context", "item text is a number", _set("items/0/text", 1)),
    ("assembled_context", "item has unknown field", _set("items/0/file", "a.py")),
    ("assembled_context", "position out of order", _set("items/0/provenance/position", 1)),
    ("assembled_context", "count exceeds budget", _set("token_count", 257)),
    ("assembled_context", "count negative", _set("token_count", -1)),
    ("assembled_context", "tokenizer is empty", _set("tokenizer", "")),
    ("assembled_context", "abstained is a string", _set("abstained", "false")),
    ("assembled_context", "abstained with items", _set("abstained", True)),
    ("assembled_context", "degraded is a boolean", _set("degraded", True)),
    ("assembled_context", "degraded is a string", _set("degraded", "selector")),
    ("assembled_context", "degraded names unknown stage", _set("degraded", ["reader"])),
    ("assembled_context", "degraded repeats a stage", _set("degraded", ["selector", "selector"])),
    ("assembled_context", "query_id not an identity", _set("query_id", "q1")),
    ("assembled_context", "bundle_version is null", _set("bundle_version", None)),
    ("assembled_context", "ordering_policy is empty", _set("ordering_policy", "")),
]


@pytest.mark.parametrize(
    ("name", "mutate"),
    [pytest.param(name, mutate, id=f"{name}: {label}") for name, label, mutate in MALFORMED],
)
def test_malformed_record_is_rejected_with_its_typed_error(name: str, mutate: Mutation) -> None:
    record_type, _ = GOLDEN_CASES[name]
    record = golden_dict(name)
    mutate(record)
    with pytest.raises(MalformedRecordError) as caught:
        record_type.from_dict(record)
    assert isinstance(caught.value, ContractError)
    assert not isinstance(caught.value, SchemaVersionError)
    with pytest.raises(MalformedRecordError):
        record_type.from_json(json.dumps(record))


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_malformed_json_is_rejected(name: str) -> None:
    record_type, _ = GOLDEN_CASES[name]
    raw = golden_bytes(name)
    text = raw.decode("utf-8")
    duplicate_key = '{"schema_version":1,' + text[1:]
    bad_inputs: list[str | bytes] = [
        b"",
        b"null",
        b"[]",
        b'"record"',
        b"{",
        raw[:-1],
        raw + b"}",
        b"\xff\xfe" + raw,
        b"\xef\xbb\xbf" + raw,  # byte order mark
        text.encode("utf-16"),
        duplicate_key,
        text.replace('"schema_version":1', '"schema_version":NaN'),
        text.replace('"schema_version":1', '"schema_version":1e999'),
    ]
    for bad in bad_inputs:
        with pytest.raises(MalformedRecordError):
            record_type.from_json(bad)


def test_non_finite_numbers_are_rejected() -> None:
    candidate = golden_dict("candidate")
    text = json.dumps(candidate)
    for token in ("NaN", "Infinity", "-Infinity", "1e999"):
        with pytest.raises(MalformedRecordError):
            Candidate.from_json(text.replace("12.5", token))
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(MalformedRecordError):
            Candidate(item=golden_item(), features=golden_candidate().features, scores={"x": value})
        with pytest.raises(MalformedRecordError):
            MemoryItem.create(
                scope="s",
                kind="note",
                text="t",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                source=Source("o", "r"),
                metadata={"weight": value},
            )


def test_constructors_reject_what_decoding_rejects() -> None:
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    source = Source("synth", "r")
    item = golden_item()
    with pytest.raises(MalformedRecordError):  # a naive datetime is not an instant
        MemoryItem.create(
            scope="s", kind="note", text="t", created_at=datetime(2026, 1, 1), source=source
        )
    with pytest.raises(MalformedRecordError):  # text UTF-8 cannot carry
        MemoryItem.create(scope="s", kind="note", text="\ud800", created_at=aware, source=source)
    with pytest.raises(MalformedRecordError):
        MemoryItem.create(scope="", kind="note", text="t", created_at=aware, source=source)
    with pytest.raises(MalformedRecordError):
        MemoryItem.create(
            scope="s",
            kind="note",
            text="t",
            created_at=aware,
            source=source,
            metadata={"when": aware},  # not a JSON value
        )
    with pytest.raises(MalformedRecordError):  # an id that is not derived from the content
        dataclasses.replace(item, text="changed")
    with pytest.raises(SchemaVersionError):
        dataclasses.replace(item, schema_version=2)
    with pytest.raises(MalformedRecordError):
        Query.create(text="q", scope="", reference_time=aware, token_budget=1)
    with pytest.raises(MalformedRecordError):
        Query.create(text="q", scope="s", reference_time=aware, token_budget=-1)
    with pytest.raises(MalformedRecordError):
        Filters(kinds=())
    with pytest.raises(MalformedRecordError):
        Span(3, 2)
    with pytest.raises(MalformedRecordError):
        Provenance(item_id=item.id, source=source, position=0, token_count=1, truncated=True)
    with pytest.raises(MalformedRecordError):
        dataclasses.replace(golden_context(), token_count=10_000)
    with pytest.raises(MalformedRecordError):
        dataclasses.replace(golden_context(), abstained=True)


def test_records_are_immutable() -> None:
    item = golden_item()
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.text = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        item.metadata["domain"] = "coding"  # type: ignore[index]
    assert isinstance(item.metadata["tags"], tuple)
    candidate = golden_candidate()
    with pytest.raises(TypeError):
        candidate.scores["bm25"] = 0.0  # type: ignore[index]
    # A record does not alias the caller's containers.
    metadata: dict[str, object] = {"tags": ["a"]}
    held = MemoryItem.create(
        scope="s",
        kind="note",
        text="t",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        source=Source("o", "r"),
        metadata=metadata,
    )
    before = held.to_bytes()
    metadata["tags"] = ["b"]
    metadata["new"] = 1
    assert held.to_bytes() == before


def test_abstaining_and_degraded_contexts() -> None:
    query = golden_query()
    abstained = AssembledContext(
        query_id=query.id,
        items=(),
        token_count=0,
        token_budget=query.token_budget,
        tokenizer=default_tokenizer().id,
        abstained=True,
        degraded=(),
        bundle_version="bundle-0.0.1",
        ordering_policy="chronological",
    )
    assert AssembledContext.from_json(abstained.to_bytes()) == abstained
    assert b'"abstained":true' in abstained.to_bytes()
    assert b'"degraded":[]' in abstained.to_bytes()
    # Stage names are held in pipeline order whatever order they were reported in.
    degraded = dataclasses.replace(
        golden_context(), degraded=("assembler", "retriever", "selector")
    )
    assert degraded.degraded == ("retriever", "selector", "assembler")
    assert AssembledContext.from_json(degraded.to_bytes()) == degraded


def test_optional_parts_may_be_absent_and_are_written_canonically() -> None:
    item = golden_dict("memory_item")
    del item["metadata"]
    assert MemoryItem.from_dict(item).to_dict()["metadata"] == {}
    query = golden_dict("query")
    del query["metadata"]
    query["filters"] = {"created_after": "2026-01-01T00:00:00.000000Z", "kinds": ["note", "fact"]}
    decoded = Query.from_dict(query)
    assert decoded.id == golden_query().id
    assert decoded.filters == golden_query().filters
    bare = Query.create(
        text="q", scope="s", reference_time=datetime(2026, 1, 1, tzinfo=UTC), token_budget=0
    )
    record = bare.to_dict()
    assert record["filters"] == {"created_after": None, "created_before": None, "kinds": None}
    del record["filters"]
    assert Query.from_dict(record) == bare


# -------------------------------------------------- no required field is domain-specific

# Clause 1.1: the fields a memory item must have, and nothing else.
CLAUSE_1_1_FIELDS = {"id", "scope", "kind", "text", "created_at", "source"}
CLAUSE_1_1_DEFAULTED = {"metadata", "schema_version"}


def build_item(
    *,
    scope: str,
    kind: str,
    text: str,
    created_at: datetime,
    origin: str,
    ref: str,
    metadata: Mapping[str, object] | None = None,
) -> MemoryItem:
    """The single path by which both domains make items in this test."""
    return MemoryItem.create(
        scope=scope,
        kind=kind,
        text=text,
        created_at=created_at,
        source=Source(origin=origin, ref=ref),
        metadata=metadata,
    )


def items_from_coding_episode(episode: Mapping[str, Any], with_metadata: bool) -> list[MemoryItem]:
    items = []
    for step in episode["steps"]:
        if step["role"] == "user":
            kind, text = "user_message", step["content"]
        elif step["role"] == "tool":
            kind = "file_content" if step["tool"] == "read_file" else "tool_output"
            text = step["output"]
        elif "tool" in step:
            kind, text = "tool_call", json.dumps(
                {"tool": step["tool"], "arguments": step["arguments"]}, sort_keys=True
            )
        else:
            kind, text = "assistant_message", step["content"]
        extras = {
            key: step[key] for key in ("tool", "path", "exit_code", "arguments") if key in step
        }
        extras["repository"] = episode["repository"]
        items.append(
            build_item(
                scope=episode["session_id"],
                kind=kind,
                text=text,
                created_at=datetime.fromisoformat(step["ts"]),
                origin="bucket",
                ref=f"{episode['session_id']}/{step['index']}",
                metadata=extras if with_metadata else None,
            )
        )
    return items


def items_from_conversation(session: Mapping[str, Any], with_metadata: bool) -> list[MemoryItem]:
    scope = f"{session['persona']}"
    items = []
    for position, turn in enumerate(session["turns"]):
        extras = {"session": session["session"], **{k: turn[k] for k in ("mood",) if k in turn}}
        items.append(
            build_item(
                scope=scope,
                kind=f"{turn['speaker']}_message",
                text=turn["utterance"],
                created_at=datetime.fromisoformat(turn["said_at"]),
                origin="synth",
                ref=f"{session['persona']}/{session['session']}/turn-{position}",
                metadata=extras if with_metadata else None,
            )
        )
    for position, fact in enumerate(session["facts"]):
        items.append(
            build_item(
                scope=scope,
                kind=fact.get("category", "fact"),
                text=fact["statement"],
                created_at=datetime.fromisoformat(fact["learned_at"]),
                origin="synth",
                ref=f"{session['persona']}/{session['session']}/fact-{position}",
                metadata={"turn": fact["turn"]} if with_metadata else None,
            )
        )
    return items


def load_fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return loaded


def domain_items(with_metadata: bool) -> dict[str, list[MemoryItem]]:
    return {
        "coding": items_from_coding_episode(load_fixture("coding_episode.json"), with_metadata),
        "conversation": items_from_conversation(
            load_fixture("conversational_session.json"), with_metadata
        ),
    }


def test_required_fields_are_exactly_those_of_clause_1_1() -> None:
    fields = dataclasses.fields(MemoryItem)
    required = {
        f.name
        for f in fields
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    assert required == CLAUSE_1_1_FIELDS
    assert {f.name for f in fields} - required == CLAUSE_1_1_DEFAULTED
    assert {f.name for f in dataclasses.fields(Source)} == {"origin", "ref"}
    # The one constructor takes the same content for every domain; only metadata is optional.
    parameters = inspect.signature(MemoryItem.create).parameters
    needed = {name for name, p in parameters.items() if p.default is inspect.Parameter.empty}
    assert needed == CLAUSE_1_1_FIELDS - {"id"}
    assert set(parameters) - needed == {"metadata"}


def test_one_constructor_serves_a_coding_episode_and_a_conversational_session() -> None:
    with_metadata = domain_items(with_metadata=True)
    without_metadata = domain_items(with_metadata=False)
    assert len(with_metadata["coding"]) == 6
    assert len(with_metadata["conversation"]) == 5
    for domain, items in with_metadata.items():
        bare_items = without_metadata[domain]
        assert {type(item) for item in items} == {MemoryItem}
        for item, bare in zip(items, bare_items, strict=True):
            # Domain detail lives only in metadata: dropping all of it leaves a valid
            # item with the same identity, so no required field is domain-specific.
            assert bare.metadata == {}
            assert bare.id == item.id
            record = item.to_dict()
            assert set(record) == CLAUSE_1_1_FIELDS | CLAUSE_1_1_DEFAULTED
            assert set(bare.to_dict()) == set(record)
            assert MemoryItem.from_json(item.to_bytes()) == item
            del record["metadata"]
            assert MemoryItem.from_dict(record) == bare
    # Both domains draw on one vocabulary, and a kind outside it is accepted as well.
    kinds = {item.kind for items in with_metadata.values() for item in items}
    assert kinds - set(CORE_KINDS) == set()
    assert {item.kind for item in with_metadata["coding"]} >= {"tool_call", "file_content"}
    assert {item.kind for item in with_metadata["conversation"]} >= {"preference", "fact"}
    open_kind = build_item(
        scope="s",
        kind="calendar_entry",
        text="t",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        origin="o",
        ref="",
    )
    assert MemoryItem.from_json(open_kind.to_bytes()).kind == "calendar_entry"


def test_downstream_types_are_built_the_same_way_for_both_domains() -> None:
    tokenizer = default_tokenizer()
    for items in domain_items(with_metadata=True).values():
        newest = max(item.created_at for item in items)
        query = Query.create(
            text="What changed most recently?",
            scope=items[0].scope,
            reference_time=newest,
            token_budget=64,
        )
        entries: list[ContextItem] = []
        used = 0
        for item in items:
            candidate = Candidate(
                item=item,
                features=Features(
                    token_count=count_tokens(item.text, tokenizer),
                    recency_seconds=(query.reference_time - item.created_at).total_seconds(),
                ),
                scores={"bm25": 1.0},
            )
            assert Candidate.from_json(candidate.to_bytes()) == candidate
            cost = candidate.features.token_count
            if used + cost > query.token_budget:
                continue
            entries.append(
                ContextItem(
                    text=item.text,
                    provenance=Provenance(
                        item_id=item.id,
                        source=item.source,
                        position=len(entries),
                        token_count=cost,
                        scores=candidate.scores,
                    ),
                )
            )
            used += cost
        rendered = "\n".join(entry.text for entry in entries)
        context = AssembledContext(
            query_id=query.id,
            items=tuple(entries),
            token_count=count_tokens(rendered, tokenizer),
            token_budget=query.token_budget,
            tokenizer=tokenizer.id,
            abstained=False,
            degraded=(),
            bundle_version="none",
            ordering_policy="chronological",
        )
        assert entries
        assert AssembledContext.from_json(context.to_bytes()) == context


ALPHABET = "abcXYZ 019_-./\n\t\"\\{}[]:,éö\u0301\u0308ßЖ中文🥛\u00a0"


def _random_text(rng: random.Random, low: int = 0, high: int = 40) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(low, high)))


def _random_json(rng: random.Random, depth: int = 0) -> object:
    roll = rng.random()
    if depth >= 2 or roll < 0.5:
        choices: list[object] = [
            None,
            rng.random() < 0.5,
            rng.randint(-(10**12), 10**12),
            rng.uniform(-1e6, 1e6),
            _random_text(rng),
        ]
        return rng.choice(choices)
    if roll < 0.75:
        return [_random_json(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {_random_text(rng, 1, 6): _random_json(rng, depth + 1) for _ in range(rng.randint(0, 3))}


def test_property_any_content_round_trips_and_metadata_never_enters_identity() -> None:
    rng = random.Random(20261110)
    domain_shapes: list[Callable[[], dict[str, object]]] = [
        lambda: {"tool": _random_text(rng), "exit_code": rng.randint(0, 255)},
        lambda: {"persona": _random_text(rng), "mood": _random_text(rng)},
        lambda: {_random_text(rng, 1, 6): _random_json(rng)},
        dict,
    ]
    for _ in range(300):
        offset = timezone(timedelta(minutes=rng.randint(-14 * 60, 14 * 60)))
        created_at = datetime(2000, 1, 1, tzinfo=offset) + timedelta(
            microseconds=rng.randint(0, 40 * 365 * 86_400 * 10**6)
        )
        content: dict[str, Any] = {
            "scope": _random_text(rng, 1, 12),
            "kind": rng.choice([*CORE_KINDS, _random_text(rng, 1, 12)]),
            "text": _random_text(rng, 0, 200),
            "created_at": created_at,
            "origin": rng.choice(["bucket", "synth", _random_text(rng, 1, 8)]),
            "ref": _random_text(rng),
        }
        metadata_a = rng.choice(domain_shapes)()
        metadata_b = rng.choice(domain_shapes)()
        first = build_item(**content, metadata=metadata_a)
        second = build_item(**content, metadata=metadata_b)
        bare = build_item(**content)
        assert first.id == second.id == bare.id
        encoded = first.to_bytes()
        decoded = MemoryItem.from_json(encoded)
        assert decoded == first
        assert decoded.to_bytes() == encoded
        assert all_strings_are_nfc(json.loads(encoded))
        assert first.created_at == created_at
        assert first.created_at.utcoffset() == timedelta(0)
        # Decomposed input is the same content.
        decomposed = {**content, "text": unicodedata.normalize("NFD", content["text"])}
        assert build_item(**decomposed).id == first.id
        # Any change of text is a change of identity.
        assert build_item(**{**content, "text": content["text"] + "x"}).id != first.id


# --------------------------------------------------------------------------- tokenizer

# Hand-counted under the stand-in rule: one token per four word characters (rounded
# up per run), one per other non-space character, whitespace free.
TOKEN_VECTORS = [
    ("", 0),
    ("   \n\t", 0),
    ("a", 1),
    ("abcd", 1),
    ("hello", 2),
    ("12345678", 2),
    ("123456789", 3),
    ("hello, world!", 6),
    ("a_b c", 2),
    ("naïve café", 3),
    ("def spin(path):", 6),
    ("☕", 1),
    ("中文中文中", 2),
    ("x = y+1", 5),
]


def test_default_tokenizer_is_declared_and_conforms() -> None:
    tokenizer = default_tokenizer()
    assert isinstance(tokenizer, Tokenizer)
    assert isinstance(tokenizer, StandInTokenizer)
    assert tokenizer.id == "trina-standin-v1"
    assert default_tokenizer().id == tokenizer.id


@pytest.mark.parametrize(("text", "expected"), TOKEN_VECTORS)
def test_token_count_vectors(text: str, expected: int) -> None:
    tokenizer = default_tokenizer()
    assert tokenizer.count(text) == expected
    assert count_tokens(text, tokenizer) == expected


def test_truncate_keeps_a_head_within_the_limit_and_names_its_span() -> None:
    tokenizer = default_tokenizer()
    text = "def spin(path):\n    return path.split('/')[-1]\n"
    total = tokenizer.count(text)
    assert tokenizer.truncate(text, 0) == ("", Span(0, 0))
    assert tokenizer.truncate(text, 3) == ("def spin(", Span(0, 9))
    assert tokenizer.truncate(text, total) == (text, Span(0, len(text)))
    assert tokenizer.truncate(text, total + 5) == (text, Span(0, len(text)))
    assert tokenizer.truncate("123456789", 2) == ("12345678", Span(0, 8))
    with pytest.raises(TokenizerError):
        tokenizer.truncate(text, -1)
    rng = random.Random(4)
    for _ in range(300):
        sample = _random_text(rng, 0, 80)
        limit = rng.randint(0, 30)
        kept, span = tokenizer.truncate(sample, limit)
        assert kept == sample[span.start : span.end]
        assert count_tokens(kept, tokenizer) <= limit
        assert count_tokens(kept, tokenizer) == min(limit, count_tokens(sample, tokenizer))


class _FixedTokenizer:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls: list[str] = []

    @property
    def id(self) -> str:
        return "fixed-v1"

    def count(self, text: str) -> int:
        self.calls.append(text)
        result: int = self.value
        return result

    def truncate(self, text: str, limit: int) -> tuple[str, Span]:
        return text, Span(0, len(text))


def test_count_tokens_is_the_single_gate_to_the_declared_tokenizer() -> None:
    declared = _FixedTokenizer(7)
    assert isinstance(declared, Tokenizer)
    assert count_tokens("some rendered context", declared) == 7
    assert declared.calls == ["some rendered context"]
    for invalid in (-1, 2.5, None, True, "3"):
        with pytest.raises(TokenizerError):
            count_tokens("text", _FixedTokenizer(invalid))
    with pytest.raises(TokenizerError):
        count_tokens(b"bytes", declared)  # type: ignore[arg-type]
    # The tokenizer is always declared by the caller: there is no implicit fallback.
    parameters = inspect.signature(count_tokens).parameters
    assert list(parameters) == ["text", "tokenizer"]
    assert all(p.default is inspect.Parameter.empty for p in parameters.values())


def test_the_package_declares_one_count_function() -> None:
    definitions = [
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if "def count_tokens(" in path.read_text(encoding="utf-8")
    ]
    assert definitions == ["schema/tokenizer.py"]
    import trina.schema
    import trina.schema.tokenizer

    assert trina.schema.count_tokens is trina.schema.tokenizer.count_tokens
