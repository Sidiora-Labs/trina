"""The run record: what a run was, and what it measured (clauses 11.1 and 11.6).

Layout under the ignored ``runs/`` directory (training.md section 3)::

    runs/<run_id>/
      config.resolved.json     the frozen document, in canonical bytes
      record.json              identity and provenance
      metrics.jsonl            append-only metrics, one JSON object per line
      checkpoints/index.jsonl  append-only checkpoint index
      checkpoints/step-<n>/    one directory per checkpoint
      SHA256SUMS               digest of every file above, written at close

Everything is a local file in an open format; no service is contacted. Writing the
checkpoint directories themselves, atomically, belongs to the training core; this
module indexes them.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from trina.config import SEED_NAMES, ConfigError, ResolvedConfig
from trina.runs.errors import RunRecordError
from trina.runs.provenance import HARDWARE_KEYS, CodeRevision, sha256_file
from trina.schema.canonical import format_instant, freeze_map, parse_json, thaw_json
from trina.schema.errors import MalformedRecordError

RECORD_VERSION = 1
STATUSES = ("running", "completed", "failed", "aborted_memory_limit", "failing_baseline")
# Record fields filled in by the training core while the run is open.
ANNOTATIONS = ("limits", "device", "nondeterminism")

CONFIG_FILE = "config.resolved.json"
RECORD_FILE = "record.json"
METRICS_FILE = "metrics.jsonl"
CHECKPOINTS_DIR = "checkpoints"
INDEX_FILE = "checkpoints/index.jsonl"
SUMS_FILE = "SHA256SUMS"

_STAGE_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
_METRIC_KEYS = ("t", "step", "split", "name", "value", "domain", "resume")
_CHECKPOINT_KEYS = ("t", "step", "files", "config_digest", "resume")

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_count(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_label(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _frozen(where: str, value: object) -> Mapping[str, object]:
    try:
        return freeze_map("record", where, value)
    except MalformedRecordError as exc:
        raise RunRecordError(f"{exc.path}: {exc.reason}") from None


@dataclass(frozen=True)
class Metric:
    """One line of ``metrics.jsonl``. ``domain`` is None for a metric over all domains."""

    t: str
    step: int
    split: str
    name: str
    value: int | float
    domain: str | None
    resume: int

    def __post_init__(self) -> None:
        value = self.value
        if (
            not isinstance(self.t, str)
            or not _is_count(self.step)
            or not _is_label(self.split)
            or not _is_label(self.name)
            or isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or not (self.domain is None or _is_label(self.domain))
            or not _is_count(self.resume)
        ):
            raise RunRecordError("metric: a field is missing, of the wrong type or not finite")

    @property
    def key(self) -> tuple[int, str, str, str | None]:
        return (self.step, self.split, self.name, self.domain)

    def to_line(self) -> str:
        fields = {key: getattr(self, key) for key in _METRIC_KEYS}
        return json.dumps(fields, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@dataclass(frozen=True)
class Checkpoint:
    """One line of ``checkpoints/index.jsonl``: a step and the digests of its files."""

    t: str
    step: int
    files: Mapping[str, str]
    config_digest: str
    resume: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.t, str)
            or not _is_count(self.step)
            or not isinstance(self.config_digest, str)
            or not _is_count(self.resume)
        ):
            raise RunRecordError("checkpoint: a field is missing or of the wrong type")
        files = _frozen("checkpoint.files", self.files)
        if not files or not all(isinstance(digest, str) for digest in files.values()):
            raise RunRecordError("checkpoint: files must map each file to its digest")
        object.__setattr__(self, "files", files)

    def to_line(self) -> str:
        fields = {key: thaw_json(getattr(self, key)) for key in _CHECKPOINT_KEYS}
        return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class RunRecord:
    """``record.json``: the identity and provenance of one run."""

    run_id: str
    stage: str
    status: str
    config_digest: str
    code_revision: CodeRevision
    dataset_version: str
    label_set_version: str | None
    seeds: Mapping[str, int]
    hardware: Mapping[str, object]
    parents: tuple[str, ...]
    started_at: str
    ended_at: str | None
    resumes: int
    limits: Mapping[str, object] | None = None
    device: Mapping[str, object] | None = None
    nondeterminism: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise RunRecordError("record: status must be one of " + ", ".join(STATUSES))
        for name in ("run_id", "stage", "config_digest", "dataset_version", "started_at"):
            if not _is_label(getattr(self, name)):
                raise RunRecordError(f"record: {name} must be a non-empty string")
        for name in ("label_set_version", "ended_at"):
            if not isinstance(getattr(self, name), str | None):
                raise RunRecordError(f"record: {name} must be a string or null")
        if not isinstance(self.code_revision, CodeRevision):
            raise RunRecordError("record: code_revision is missing")
        seeds = _frozen("seeds", self.seeds)
        if tuple(sorted(seeds)) != tuple(sorted(SEED_NAMES)) or not all(
            type(seed) is int for seed in seeds.values()
        ):
            raise RunRecordError("record: seeds must hold integers " + ", ".join(SEED_NAMES))
        hardware = _frozen("hardware", self.hardware)
        absent = sorted(set(HARDWARE_KEYS) - set(hardware))
        if absent:
            raise RunRecordError("record: hardware lacks " + ", ".join(absent))
        if not all(_is_label(parent) for parent in self.parents):
            raise RunRecordError("record: parents must be run identities")
        if not _is_count(self.resumes):
            raise RunRecordError("record: resumes must be a count")
        object.__setattr__(self, "seeds", seeds)
        object.__setattr__(self, "hardware", hardware)
        object.__setattr__(self, "parents", tuple(self.parents))
        for name in ANNOTATIONS:
            facts = getattr(self, name)
            if facts is not None:
                object.__setattr__(self, name, _frozen(name, facts))

    def to_json(self) -> dict[str, object]:
        fields = {
            field.name: thaw_json(getattr(self, field.name)) for field in dataclasses.fields(self)
        }
        return {
            "record_version": RECORD_VERSION,
            **fields,
            "code_revision": self.code_revision.to_json(),
        }

    @classmethod
    def from_json(cls, value: object) -> RunRecord:
        names = {field.name for field in dataclasses.fields(cls)}
        if not isinstance(value, dict) or set(value) != names | {"record_version"}:
            raise RunRecordError("record.json: unknown or missing field")
        if value["record_version"] != RECORD_VERSION:
            raise RunRecordError("record.json: unknown record_version")
        fields = {name: value[name] for name in names}
        fields["code_revision"] = CodeRevision.from_json(fields["code_revision"])
        if not isinstance(fields["parents"], list):
            raise RunRecordError("record.json: parents must be a list")
        return cls(**fields)


def effective_metrics(metrics: tuple[Metric, ...]) -> tuple[Metric, ...]:
    """Drop superseded lines: the last line per (step, split, name, domain) stands."""
    last: dict[tuple[int, str, str, str | None], Metric] = {}
    for metric in metrics:
        last.pop(metric.key, None)
        last[metric.key] = metric
    return tuple(last.values())


@dataclass(frozen=True)
class LoadedRun:
    """A run record read back from disk."""

    run_dir: Path
    config: ResolvedConfig
    record: RunRecord
    metrics: tuple[Metric, ...]
    checkpoints: tuple[Checkpoint, ...]

    def effective_metrics(self) -> tuple[Metric, ...]:
        return effective_metrics(self.metrics)


def _identity(config: ResolvedConfig) -> tuple[str, str, str | None, dict[str, int]]:
    """Stage, dataset version, label-set version and seeds, as the configuration pins them."""
    try:
        label_key = "run.label_set_version"
        return (
            config.get_str("run.stage"),
            config.get_str("run.dataset_version"),
            config.get_str(label_key) if label_key in config else None,
            {name: config.get_int(f"seeds.{name}") for name in SEED_NAMES},
        )
    except ConfigError as exc:
        raise RunRecordError(f"configuration does not pin the run identity: {exc}") from None


def _write_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _append_line(path: Path, line: str) -> None:
    with path.open("ab") as handle:
        handle.write(line.encode("utf-8") + b"\n")


def _split_lines(path: Path) -> tuple[list[bytes], bytes]:
    """Return the complete lines of a JSONL file and any unterminated tail."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RunRecordError(f"{path.name}: cannot be read ({type(exc).__name__})") from None
    body, newline, tail = data.rpartition(b"\n")
    return (body.split(b"\n") if newline else []), tail


def _read_jsonl(path: Path, keys: tuple[str, ...], torn_ok: bool) -> list[dict[str, object]]:
    lines, tail = _split_lines(path)
    if tail and not torn_ok:
        raise RunRecordError(f"{path.name}: the last line is incomplete")
    rows: list[dict[str, object]] = []
    for number, line in enumerate(lines, start=1):
        try:
            row = parse_json("record", line)
        except MalformedRecordError as exc:
            raise RunRecordError(f"{path.name} line {number}: {exc.reason}") from None
        if not isinstance(row, dict) or set(row) != set(keys):
            raise RunRecordError(f"{path.name} line {number}: unknown or missing field")
        rows.append(row)
    return rows


def _listed_files(run_dir: Path) -> list[str]:
    return sorted(
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file() and path.name != SUMS_FILE
    )


def _check_sums(run_dir: Path) -> None:
    try:
        text = (run_dir / SUMS_FILE).read_text(encoding="utf-8")
    except OSError:
        raise RunRecordError(f"{SUMS_FILE}: a closed run must have one") from None
    listed: dict[str, str] = {}
    for line in text.splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or name in listed:
            raise RunRecordError(f"{SUMS_FILE}: malformed line")
        listed[name] = digest
    present = _listed_files(run_dir)
    if sorted(listed) != present:
        raise RunRecordError(f"{SUMS_FILE}: the listed files are not the files present")
    for name in present:
        if sha256_file(run_dir / name) != listed[name]:
            raise RunRecordError(f"{SUMS_FILE}: {name} does not match its digest")


def load_run(run_dir: Path, *, verify: bool = True) -> LoadedRun:
    """Read a run record back from local files only.

    The stored configuration must be in canonical form and match the record's digest,
    stage, dataset version and seeds. With ``verify`` a closed run must also match its
    ``SHA256SUMS``. A run still marked ``running`` has no sums yet, and an unterminated
    last line (a write cut short by a kill) is ignored for it alone.
    """
    try:
        config_bytes = (run_dir / CONFIG_FILE).read_bytes()
        record_bytes = (run_dir / RECORD_FILE).read_bytes()
    except OSError as exc:
        raise RunRecordError(f"run record is incomplete ({type(exc).__name__})") from None
    try:
        config = ResolvedConfig.from_bytes(config_bytes)
        record = RunRecord.from_json(parse_json("record", record_bytes))
    except (ConfigError, MalformedRecordError) as exc:
        raise RunRecordError(f"run record is malformed: {exc}") from None
    stage, dataset_version, label_set_version, seeds = _identity(config)
    if (
        config.digest != record.config_digest
        or stage != record.stage
        or dataset_version != record.dataset_version
        or label_set_version != record.label_set_version
        or seeds != dict(record.seeds)
    ):
        raise RunRecordError("record.json does not describe the stored configuration")
    running = record.status == "running"
    if verify and not running:
        _check_sums(run_dir)
    metrics = tuple(
        Metric(**row)  # type: ignore[arg-type]
        for row in _read_jsonl(run_dir / METRICS_FILE, _METRIC_KEYS, running)
    )
    checkpoints = tuple(
        Checkpoint(**row)  # type: ignore[arg-type]
        for row in _read_jsonl(run_dir / INDEX_FILE, _CHECKPOINT_KEYS, running)
    )
    return LoadedRun(run_dir, config, record, metrics, checkpoints)


class RunWriter:
    """Writes one run record. Use ``create`` or ``resume``, then close it.

    As a context manager it closes the run as ``completed``, or as ``failed`` when the
    block raises; ``close`` inside the block sets any other final status.
    """

    def __init__(self, run_dir: Path, record: RunRecord, clock: Clock) -> None:
        self.run_dir = run_dir
        self._record = record
        self._clock = clock

    @property
    def record(self) -> RunRecord:
        return self._record

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        config: ResolvedConfig,
        *,
        code_revision: CodeRevision,
        hardware: Mapping[str, object],
        parents: tuple[str, ...] = (),
        clock: Clock = _utc_now,
    ) -> RunWriter:
        """Start a run under ``runs_dir`` from its one resolved configuration."""
        stage, dataset_version, label_set_version, seeds = _identity(config)
        if not _STAGE_RE.fullmatch(stage):
            raise RunRecordError("run.stage must be lower-case letters, digits, '_' or '-'")
        started = clock().astimezone(UTC)
        run_id = f"{started:%Y%m%dT%H%M%SZ}-{stage}-{config.digest[:8]}"
        record = RunRecord(
            run_id=run_id,
            stage=stage,
            status="running",
            config_digest=config.digest,
            code_revision=code_revision,
            dataset_version=dataset_version,
            label_set_version=label_set_version,
            seeds=seeds,
            hardware=hardware,
            parents=parents,
            started_at=format_instant(started),
            ended_at=None,
            resumes=0,
        )
        run_dir = runs_dir / run_id
        try:
            run_dir.mkdir(parents=True)
        except FileExistsError:
            raise RunRecordError(f"run {run_id} already exists") from None
        (run_dir / CHECKPOINTS_DIR).mkdir()
        _write_atomic(run_dir / CONFIG_FILE, config.to_bytes())
        (run_dir / METRICS_FILE).touch()
        (run_dir / INDEX_FILE).touch()
        writer = cls(run_dir, record, clock)
        writer._save()
        return writer

    @classmethod
    def resume(cls, run_dir: Path, config: ResolvedConfig, *, clock: Clock = _utc_now) -> RunWriter:
        """Reopen a run to continue it; the configuration digest must be unchanged.

        Lines logged from here on carry the next ``resume`` number, and an unterminated
        last line left by a kill is removed.
        """
        loaded = load_run(run_dir, verify=False)
        if loaded.record.config_digest != config.digest:
            raise RunRecordError("resume refused: the configuration digest differs")
        (run_dir / SUMS_FILE).unlink(missing_ok=True)
        for name in (METRICS_FILE, INDEX_FILE):
            lines, tail = _split_lines(run_dir / name)
            if tail:
                _write_atomic(run_dir / name, b"".join(line + b"\n" for line in lines))
        record = dataclasses.replace(
            loaded.record, status="running", ended_at=None, resumes=loaded.record.resumes + 1
        )
        writer = cls(run_dir, record, clock)
        writer._save()
        return writer

    def _save(self) -> None:
        text = json.dumps(self._record.to_json(), ensure_ascii=False, indent=2, sort_keys=True)
        _write_atomic(self.run_dir / RECORD_FILE, text.encode("utf-8") + b"\n")

    def _open_record(self) -> RunRecord:
        if self._record.status != "running":
            raise RunRecordError("the run is closed")
        return self._record

    def log_metric(
        self, *, step: int, split: str, name: str, value: int | float, domain: str | None = None
    ) -> Metric:
        """Append one metric line to ``metrics.jsonl``."""
        record = self._open_record()
        metric = Metric(
            format_instant(self._clock()), step, split, name, value, domain, record.resumes
        )
        _append_line(self.run_dir / METRICS_FILE, metric.to_line())
        return metric

    def checkpoint_dir(self, step: int) -> Path:
        """Where the checkpoint of ``step`` lives; the caller writes it, then registers it."""
        if not _is_count(step):
            raise RunRecordError("checkpoint: step must be a count")
        return self.run_dir / CHECKPOINTS_DIR / f"step-{step}"

    def register_checkpoint(self, step: int) -> Checkpoint:
        """Index the finished checkpoint directory of ``step`` with its file digests."""
        record = self._open_record()
        directory = self.checkpoint_dir(step)
        files = {
            path.relative_to(directory).as_posix(): sha256_file(path)
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }
        if not files:
            raise RunRecordError(f"checkpoint: {directory.name} holds no file")
        checkpoint = Checkpoint(
            format_instant(self._clock()), step, files, record.config_digest, record.resumes
        )
        _append_line(self.run_dir / INDEX_FILE, checkpoint.to_line())
        return checkpoint

    def annotate(self, field: str, facts: Mapping[str, object]) -> None:
        """Set one of the ``ANNOTATIONS`` fields of the record."""
        record = self._open_record()
        if field not in ANNOTATIONS:
            raise RunRecordError("annotate: field must be one of " + ", ".join(ANNOTATIONS))
        self._record = dataclasses.replace(record, **{field: facts})  # type: ignore[arg-type]
        self._save()

    def close(self, status: str = "completed") -> RunRecord:
        """Set the final status and write ``SHA256SUMS`` over every file of the run."""
        record = self._open_record()
        if status == "running":
            raise RunRecordError("close: a final status is required")
        self._record = dataclasses.replace(
            record, status=status, ended_at=format_instant(self._clock())
        )
        self._save()
        sums = "".join(
            f"{sha256_file(self.run_dir / name)}  {name}\n" for name in _listed_files(self.run_dir)
        )
        _write_atomic(self.run_dir / SUMS_FILE, sums.encode("utf-8"))
        return self._record

    def __enter__(self) -> RunWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._record.status == "running":
            self.close("completed" if exc_type is None else "failed")
