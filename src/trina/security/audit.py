"""Residual-secret audit over a sample of a persisted dataset (clause 3.3).

Every dataset build calls :func:`audit_dataset` on what it wrote and then
:meth:`AuditReport.require_pass`, which raises on any high-confidence match so the
version is not published and its manifest is not written (security.md section 5).

Detectors, each declaring its confidence:

=====================  ==========  ================================================
``catalogue``          high        the redaction catalogue applied again: a match
                                   means the content was not redacted
``structure``          high        a header container survived
``private_key_marker`` high        any private-key marker
``held_secret``        high        a value currently held by the secrets loader
``entropy``            high / low  a high-entropy string next to a credential-like
                                   name; hexadecimal or weaker strings are low
``key_prefix``         low         a known key prefix with a short tail
=====================  ==========  ================================================

The sample is seeded and stratified; a dataset no larger than the sample size is
scanned in full. A sample cannot prove absence. The report holds identities, paths
and counts, never matched text.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Literal

from trina.security.loader import SecretsLoader
from trina.security.patterns import HEADER_KIND, REDACTION_PATTERNS, find_matches
from trina.security.redaction import RedactedRecord, RedactedText

Confidence = Literal["high", "low"]

DETECTORS = ("catalogue", "structure", "private_key_marker", "held_secret", "entropy", "key_prefix")

_KEY_MARKER_RE = re.compile(r"PRIVATE KEY-----|-----BEGIN [A-Z0-9 ]*KEY")
_KEY_PREFIX_RE = re.compile(
    r"\b(?:sk-|hf_|gh[pousr]_|github_pat_|glpat-|npm_|pypi-|xox[abeprs]-|AKIA|ASIA|AIza)"
    r"[A-Za-z0-9_-]{8,}"
)
_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/_=-]{20,}")
_CREDENTIAL_WORD_RE = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[ _-]?key|access[ _-]?key|private[ _-]?key"
    r"|credential|authorization)(?![a-z])"
)
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
# How far before a string a credential-like name counts as next to it.
_CONTEXT = 40
_HIGH_ENTROPY = 4.0
_LOW_ENTROPY = 3.0


class ResidualSecretError(Exception):
    """The audit found a high-confidence match: the build fails (clause 3.3)."""

    def __init__(self, report: AuditReport) -> None:
        self.report = report
        by_detector = ", ".join(
            f"{name}={levels['high']}" for name, levels in report.counts.items() if levels["high"]
        )
        super().__init__(
            f"residual-secret audit failed for {report.dataset}: {report.high} "
            f"high-confidence match(es) in {report.sample_size} sampled record(s) ({by_detector})"
        )


@dataclass(frozen=True)
class AuditRecord:
    """One persisted record: its identity, its stratum (source and shard), its content."""

    record_id: str
    stratum: str
    content: object


@dataclass(frozen=True)
class AuditFinding:
    record_id: str
    path: str
    detector: str
    kind: str
    confidence: Confidence


@dataclass(frozen=True)
class AuditReport:
    dataset: str
    population: int
    sample_size: int
    seed: int
    full_scan: bool
    strata: dict[str, tuple[int, int]]
    counts: dict[str, dict[str, int]]
    findings: tuple[AuditFinding, ...] = field(default=())

    @property
    def high(self) -> int:
        return sum(levels["high"] for levels in self.counts.values())

    @property
    def low(self) -> int:
        return sum(levels["low"] for levels in self.counts.values())

    @property
    def passed(self) -> bool:
        return self.high == 0

    def require_pass(self) -> None:
        """Raise ResidualSecretError on any high-confidence match."""
        if not self.passed:
            raise ResidualSecretError(self)

    def to_dict(self) -> dict[str, object]:
        """The report for a manifest: identities and counts, never matched text."""
        return {
            "dataset": self.dataset,
            "population": self.population,
            "sample_size": self.sample_size,
            "seed": self.seed,
            "full_scan": self.full_scan,
            "passed": self.passed,
            "strata": {
                name: {"population": population, "sampled": sampled}
                for name, (population, sampled) in sorted(self.strata.items())
            },
            "counts": {name: dict(levels) for name, levels in self.counts.items()},
            "findings": [
                {
                    "record_id": f.record_id,
                    "path": f.path,
                    "detector": f.detector,
                    "kind": f.kind,
                    "confidence": f.confidence,
                }
                for f in self.findings
            ],
        }


def shannon_entropy(value: str) -> float:
    """Bits per character of the character distribution of ``value``."""
    if not value:
        return 0.0
    total = len(value)
    return -sum(n / total * math.log2(n / total) for n in Counter(value).values())


def _entropy_confidence(value: str) -> Confidence | None:
    entropy = shannon_entropy(value)
    if entropy < _LOW_ENTROPY:
        return None
    classes = (
        any(c.isdigit() for c in value),
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
    )
    # Hexadecimal strings are digests and identifiers far more often than secrets.
    if entropy >= _HIGH_ENTROPY and all(classes) and not _HEX_RE.fullmatch(value):
        return "high"
    return "low"


def detect_text(text: str, loader: SecretsLoader | None) -> Iterator[tuple[str, str, Confidence]]:
    """(detector, kind, confidence) for every detector match in one string."""
    covered: list[tuple[int, int]] = []
    for match in find_matches(text, REDACTION_PATTERNS):
        covered.append((match.start, match.end))
        yield "catalogue", match.kind, "high"
    for _ in _KEY_MARKER_RE.finditer(text):
        yield "private_key_marker", "private_key", "high"
    if loader is not None:
        for _name, _offset in loader.occurrences(text):
            yield "held_secret", "held_secret", "high"

    def fresh(start: int, end: int) -> bool:
        return not any(start < c_end and c_start < end for c_start, c_end in covered)

    for candidate in _CANDIDATE_RE.finditer(text):
        if not fresh(candidate.start(), candidate.end()):
            continue
        context = text[max(0, candidate.start() - _CONTEXT) : candidate.start()]
        if _CREDENTIAL_WORD_RE.search(context) is None:
            continue
        confidence = _entropy_confidence(candidate.group(0))
        if confidence is not None:
            covered.append((candidate.start(), candidate.end()))
            yield "entropy", "high_entropy", confidence
    for prefixed in _KEY_PREFIX_RE.finditer(text):
        if fresh(prefixed.start(), prefixed.end()):
            yield "key_prefix", "key_prefix", "low"


def _strings(value: object, path: str) -> Iterator[tuple[str, str, bool]]:
    """(path, string, is_key) for every string in JSON-like content."""
    if isinstance(value, str):
        yield path, value, False
    elif isinstance(value, Mapping):
        for key, item in value.items():
            where = f"{path}.{key}" if path else str(key)
            yield where, str(key), True
            yield from _strings(item, where)
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")


def audit_content(
    record_id: str, content: object, loader: SecretsLoader | None = None
) -> list[AuditFinding]:
    """Run every detector over one record's content."""
    if isinstance(content, RedactedRecord):
        content = content.data
    elif isinstance(content, RedactedText):
        content = content.text
    findings: list[AuditFinding] = []
    for path, text, is_key in _strings(content, ""):
        if is_key and re.split(r"[^a-z0-9]+", text.lower())[-1:] == ["headers"]:
            findings.append(AuditFinding(record_id, path, "structure", HEADER_KIND, "high"))
        for detector, kind, confidence in detect_text(text, loader):
            findings.append(AuditFinding(record_id, path, detector, kind, confidence))
    return findings


def _rank(seed: int, record_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{record_id}".encode()).hexdigest()


def _quotas(populations: Mapping[str, int], size: int) -> dict[str, int]:
    """Proportional allocation of ``size`` over strata, at least one each when it fits."""
    total = sum(populations.values())
    names = sorted(populations)
    floor = 1 if size >= len(names) else 0
    exact = {name: size * populations[name] / total for name in names}
    quota = {name: min(populations[name], max(floor, int(exact[name]))) for name in names}
    while sum(quota.values()) < size:
        name = max(
            (n for n in names if quota[n] < populations[n]),
            key=lambda n: (exact[n] - quota[n], populations[n], n),
        )
        quota[name] += 1
    while sum(quota.values()) > size:
        name = max((n for n in names if quota[n] > floor), key=lambda n: (quota[n] - exact[n], n))
        quota[name] -= 1
    return quota


def audit_dataset(
    dataset: str,
    records: Iterable[AuditRecord],
    *,
    seed: int,
    sample_size: int | None,
    loader: SecretsLoader | None = None,
) -> AuditReport:
    """Audit a seeded, stratified sample of a persisted dataset.

    ``sample_size`` is the number of records to scan; None scans every record, and a
    dataset no larger than it is scanned in full. The sample is a function of the
    seed and the record identities only, not of the order of ``records``. ``records``
    is read once; memory is bounded by the sample size per stratum. ``loader`` adds
    the comparison with the secret values it holds.
    """
    if sample_size is not None and sample_size < 1:
        raise ValueError("sample_size must be at least 1, or None for a full scan")
    populations: Counter[str] = Counter()
    kept: dict[str, list[tuple[str, AuditRecord]]] = {}
    for record in records:
        populations[record.stratum] += 1
        bucket = kept.setdefault(record.stratum, [])
        bucket.append((_rank(seed, record.record_id), record))
        if sample_size is not None and len(bucket) > 2 * sample_size:
            bucket.sort(key=lambda item: item[0])
            del bucket[sample_size:]
    population = sum(populations.values())
    full_scan = sample_size is None or population <= sample_size
    if full_scan:
        quota = dict(populations)
    else:
        assert sample_size is not None
        quota = _quotas(populations, sample_size)
    counts = {name: {"high": 0, "low": 0} for name in DETECTORS}
    findings: list[AuditFinding] = []
    sampled = 0
    for stratum in sorted(kept):
        chosen = sorted(kept[stratum], key=lambda item: item[0])[: quota[stratum]]
        sampled += len(chosen)
        for _, record in chosen:
            for finding in audit_content(record.record_id, record.content, loader):
                counts[finding.detector][finding.confidence] += 1
                findings.append(finding)
    return AuditReport(
        dataset=dataset,
        population=population,
        sample_size=sampled,
        seed=seed,
        full_scan=full_scan,
        strata={name: (populations[name], quota[name]) for name in populations},
        counts=counts,
        findings=tuple(findings),
    )
