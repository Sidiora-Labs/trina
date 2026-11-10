"""Task 0.3: configuration resolution and run records (case TRM-002).

Layered fixtures are written as TOML files and resolved on top of the tracked
``configs/base/`` layer. The digest is recomputed from a hand-written canonical
preimage and in fresh interpreters with other hash seeds. The run record comes from
a real toy run, a seeded least-squares fit by gradient descent, executed and reloaded
with every socket disabled.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import random
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import pytest

from trina.config import (
    BASE_SCHEMA,
    SEED_NAMES,
    ConfigError,
    ConfigValueError,
    Layer,
    LayerError,
    MissingKeyError,
    ResolvedConfig,
    Schema,
    SchemaError,
    UnknownKeyError,
    load_layer,
    parse_overrides,
    resolve,
    resolve_paths,
)
from trina.runs import (
    CPU_DEVICE,
    HARDWARE_KEYS,
    STATUSES,
    CodeRevision,
    LoadedRun,
    Metric,
    RunRecordError,
    RunWriter,
    detect_code_revision,
    detect_hardware,
    load_run,
)

REPO = Path(__file__).resolve().parents[2]
BASE_DIR = REPO / "configs" / "base"
SRC = REPO / "src" / "trina"

# The toy stage declares its own section on top of the base keys, as every package
# that owns a configuration section does.
TOY_SCHEMA = BASE_SCHEMA.extend(
    {
        "toy.steps": "int",
        "toy.examples": "int",
        "toy.learning_rate": "float",
        "toy.checkpoint_every": "int",
        "toy.true_weights": "list[float]",
        "toy.eval_domains": "list[str]",
        "host.threads": "int",
        "host.deterministic": "bool",
        "mix.weights": "map[float]",
    }
)

STAGE_TOML = """\
# Stage layer of the toy run.
[run]
stage = "toy"
dataset_version = "toy-fixture-v1"

[toy]
steps = 40
examples = 32
learning_rate = 0.05
checkpoint_every = 20
true_weights = [2.0, -1.0, 0.5]
eval_domains = ["coding", "conversation"]

[host]
threads = 8
deterministic = false

[mix.weights]
coding = 0.5
conversation = 0.5
"""

HOST_TOML = """\
[host]
threads = 1
deterministic = true
"""

OVERRIDES = ("toy.learning_rate=0.1", "seeds.init=7")

# The canonical bytes of the resolved fixture, written by hand: keys sorted at every
# level, no whitespace, floats in one spelling.
EXPECTED_CANONICAL = (
    b'{"host":{"deterministic":true,"threads":1},'
    b'"mix":{"weights":{"coding":0.5,"conversation":0.5}},'
    b'"run":{"dataset_version":"toy-fixture-v1","stage":"toy"},'
    b'"seeds":{"data_order":20261111,"eval_bootstrap":20261113,"global":20261110,"init":7},'
    b'"toy":{"checkpoint_every":20,"eval_domains":["coding","conversation"],"examples":32,'
    b'"learning_rate":0.1,"steps":40,"true_weights":[2.0,-1.0,0.5]}}'
)


def write_layers(directory: Path, stage: str = STAGE_TOML, host: str = HOST_TOML) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stage_file = directory / "toy.toml"
    host_file = directory / "smoke.toml"
    stage_file.write_text(stage, encoding="utf-8")
    host_file.write_text(host, encoding="utf-8")
    return [BASE_DIR, stage_file, host_file]


@pytest.fixture
def layers(tmp_path: Path) -> list[Path]:
    return write_layers(tmp_path / "layers")


@pytest.fixture
def config(layers: list[Path]) -> ResolvedConfig:
    return resolve_paths(TOY_SCHEMA, layers, OVERRIDES)


# --------------------------------------------------------------------------- resolution


def test_layers_resolve_to_one_document_in_order(
    layers: list[Path], config: ResolvedConfig
) -> None:
    # base value kept, base value overridden on the command line
    assert config.get_int("seeds.global") == 20261110
    assert config.get_int("seeds.init") == 7
    # stage value kept, stage value replaced by the host profile, then by an override
    assert config.get_int("toy.steps") == 40
    assert config.get_int("host.threads") == 1
    assert config.get_bool("host.deterministic") is True
    assert config.get_float("toy.learning_rate") == 0.1
    assert config.get("toy.true_weights") == (2.0, -1.0, 0.5)
    assert config.get("mix.weights") == {"coding": 0.5, "conversation": 0.5}
    assert config.get_str("run.stage") == "toy"
    assert "run.stage" in config and "run.label_set_version" not in config

    assert config.sources["seeds.global"] == f"{BASE_DIR}/base.toml"
    assert config.sources["toy.steps"] == str(layers[1])
    assert config.sources["host.threads"] == str(layers[2])
    assert config.sources["seeds.init"] == "overrides"
    assert set(config.sources) == set(TOY_SCHEMA.fields)


def test_tracked_base_layer_sets_the_seeds_and_nothing_a_base_cannot_know() -> None:
    with pytest.raises(MissingKeyError) as caught:
        resolve(BASE_SCHEMA, [load_layer(BASE_DIR)])
    assert caught.value.keys == ("run.dataset_version", "run.stage")


def test_a_key_no_layer_sets_is_an_error_not_a_default(tmp_path: Path) -> None:
    stage = STAGE_TOML.replace("learning_rate = 0.05\n", "")
    with pytest.raises(MissingKeyError) as caught:
        resolve_paths(TOY_SCHEMA, write_layers(tmp_path, stage=stage))
    assert caught.value.keys == ("toy.learning_rate",)


def test_document_is_frozen(config: ResolvedConfig) -> None:
    with pytest.raises(FrozenInstanceError):
        config.document = {}  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        config.digest = "0" * 64  # type: ignore[misc]
    with pytest.raises(TypeError):
        config.document["run"] = {}  # type: ignore[index]
    toy = config.get("toy")
    with pytest.raises(TypeError):
        toy["steps"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        config.get("mix.weights")["coding"] = 1.0  # type: ignore[index]
    assert isinstance(config.get("toy.eval_domains"), tuple)


def test_typed_getters_refuse_another_kind(config: ResolvedConfig) -> None:
    with pytest.raises(ConfigError):
        config.get_int("host.deterministic")
    with pytest.raises(ConfigError):
        config.get_float("toy.steps")
    with pytest.raises(ConfigError):
        config.get("toy.absent")


# --------------------------------------------------------------------------- digest


def test_digest_is_sha256_of_the_canonical_bytes(config: ResolvedConfig) -> None:
    assert config.to_bytes() == EXPECTED_CANONICAL
    assert config.digest == hashlib.sha256(EXPECTED_CANONICAL).hexdigest()


def test_digest_ignores_spelling_order_comments_and_file_split(
    tmp_path: Path, config: ResolvedConfig
) -> None:
    # Same content: tables and keys reordered, dotted keys, comments, other number
    # spellings, and the stage split over two files of a directory layer.
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    (stage_dir / "b.toml").write_text(
        """\
mix.weights.conversation = 5e-1   # a comment
mix.weights.coding = 0.50
[toy]
eval_domains = ['coding', "conversation"]
true_weights = [2, -1.0, 5e-1]
checkpoint_every = 2_0
examples = 0x20
steps = 40
""",
        encoding="utf-8",
    )
    (stage_dir / "a.toml").write_text(
        'toy.learning_rate = 1\nrun = { dataset_version = "toy-fixture-v1", stage = "toy" }\n',
        encoding="utf-8",
    )
    host = tmp_path / "host.toml"
    host.write_text("host = { deterministic = true, threads = 1 }\n", encoding="utf-8")
    respelled = resolve_paths(
        TOY_SCHEMA, [BASE_DIR, stage_dir, host], ["seeds.init=0b111", "toy.learning_rate=1e-1"]
    )
    assert respelled.digest == config.digest
    assert respelled == config
    assert respelled.sources != config.sources  # provenance is not part of the document


def test_digest_is_the_same_in_fresh_interpreters(layers: list[Path]) -> None:
    script = (
        "import sys; from pathlib import Path; from trina.config import BASE_SCHEMA, "
        "resolve_paths; schema = BASE_SCHEMA.extend(eval(sys.argv[1])); "
        "paths = [Path(p) for p in sys.argv[2:5]]; "
        "print(resolve_paths(schema, paths, sys.argv[5:]).digest)"
    )
    extension = {
        key: kind for key, kind in TOY_SCHEMA.fields.items() if key not in BASE_SCHEMA.fields
    }
    for seed, zone, locale in (
        ("0", "UTC", "C"),
        ("1", "Asia/Tokyo", "C.UTF-8"),
        ("4242", "America/Lima", "POSIX"),
    ):
        env = {**os.environ, "PYTHONHASHSEED": seed, "TZ": zone, "LC_ALL": locale}
        done = subprocess.run(
            [sys.executable, "-c", script, repr(extension), *map(str, layers), *OVERRIDES],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert done.stdout.strip() == hashlib.sha256(EXPECTED_CANONICAL).hexdigest()


@pytest.mark.parametrize(
    "override",
    [
        "toy.steps=41",
        "toy.learning_rate=0.1000001",
        "run.stage=toy2",
        "host.deterministic=false",
        "toy.true_weights=[2.0, -1.0]",
        "mix.weights={coding = 1.0}",
        "seeds.global=1",
    ],
)
def test_digest_changes_with_any_value(
    layers: list[Path], config: ResolvedConfig, override: str
) -> None:
    key = override.partition("=")[0]
    others = [item for item in OVERRIDES if not item.startswith(key + "=")]
    assert resolve_paths(TOY_SCHEMA, layers, [*others, override]).digest != config.digest


def test_negative_zero_and_nfd_text_have_one_canonical_form(layers: list[Path]) -> None:
    plain = resolve_paths(TOY_SCHEMA, layers, ["toy.learning_rate=0.0", "run.dataset_version=café"])
    odd = resolve_paths(TOY_SCHEMA, layers, ["toy.learning_rate=-0.0", "run.dataset_version=café"])
    assert plain.digest == odd.digest
    assert b"-0.0" not in odd.to_bytes()


def test_resolved_document_reloads_from_its_bytes(config: ResolvedConfig) -> None:
    reloaded = ResolvedConfig.from_bytes(config.to_bytes())
    assert reloaded == config
    assert reloaded.digest == config.digest
    assert reloaded.to_bytes() == EXPECTED_CANONICAL
    for data in (
        EXPECTED_CANONICAL + b"\n",
        EXPECTED_CANONICAL.replace(b'"threads":1', b'"threads": 1'),
        json.dumps(json.loads(EXPECTED_CANONICAL)).encode(),
        b"[]",
        b"{",
        b'{"a":1,"a":2}',
    ):
        with pytest.raises(ConfigError):
            ResolvedConfig.from_bytes(data)


# --------------------------------------------------------------------------- rejection


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("colour = 1\n", "colour"),
        ("[toy]\nstep = 3\n", "toy.step"),
        ("[sedes]\nglobal = 1\n", "sedes"),
        ("[seeds]\nGlobal = 1\n", "seeds.Global"),
        ('"toy.steps" = 3\n', "toy.steps"),  # a quoted dotted name is not the dotted key
        ("[mix]\nweight = { coding = 1.0 }\n", "mix.weight"),
    ],
)
def test_unknown_key_in_a_file_layer_is_an_error(tmp_path: Path, text: str, key: str) -> None:
    host = tmp_path / "host.toml"
    host.write_text(text, encoding="utf-8")
    paths = write_layers(tmp_path / "layers")
    with pytest.raises(UnknownKeyError) as caught:
        resolve_paths(TOY_SCHEMA, [*paths, host])
    assert caught.value.key == key
    assert caught.value.layer == str(host)


def test_unknown_key_is_an_error_in_every_layer_position(tmp_path: Path) -> None:
    paths = write_layers(tmp_path / "layers")
    with pytest.raises(UnknownKeyError) as caught:
        resolve_paths(TOY_SCHEMA, paths, ["toy.stepz=3"])
    assert (caught.value.key, caught.value.layer) == ("toy.stepz", "overrides")
    with pytest.raises(UnknownKeyError):  # a table name is not a key
        parse_overrides(TOY_SCHEMA, ["toy=3"])

    base = tmp_path / "base"
    base.mkdir()
    (base / "base.toml").write_text((BASE_DIR / "base.toml").read_text(encoding="utf-8"))
    (base / "extra.toml").write_text("[logging]\nlevel = 'debug'\n", encoding="utf-8")
    with pytest.raises(UnknownKeyError) as caught:
        resolve_paths(TOY_SCHEMA, [base, *paths[1:]])
    assert caught.value.key == "logging"
    assert caught.value.layer == f"{base}/extra.toml"

    # A key of the toy section is unknown to a schema that does not declare it.
    with pytest.raises(UnknownKeyError):
        resolve_paths(BASE_SCHEMA, paths)


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("toy.steps = 3.0\n", "toy.steps"),
        ("toy.steps = true\n", "toy.steps"),
        ("toy.steps = '3'\n", "toy.steps"),
        ("toy.learning_rate = nan\n", "toy.learning_rate"),
        ("toy.learning_rate = inf\n", "toy.learning_rate"),
        ("toy.learning_rate = true\n", "toy.learning_rate"),
        ("toy.learning_rate = 9007199254740993\n", "toy.learning_rate"),
        ("host.deterministic = 1\n", "host.deterministic"),
        ("run.stage = 3\n", "run.stage"),
        ("toy.true_weights = 2.0\n", "toy.true_weights"),
        ("toy.true_weights = [2.0, 'x']\n", "toy.true_weights"),
        ("toy.eval_domains = [1]\n", "toy.eval_domains"),
        ("mix.weights = [0.5]\n", "mix.weights"),
        ("mix.weights = { coding = 'half' }\n", "mix.weights"),
        ("toy = 3\n", "toy"),
        ("seeds.global = 1979-05-27\n", "seeds.global"),
        ("[toy.steps]\ndeep = 1\n", "toy.steps"),  # a value key used as a table
    ],
)
def test_value_of_the_wrong_kind_is_an_error(tmp_path: Path, text: str, key: str) -> None:
    host = tmp_path / "host.toml"
    host.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigValueError) as caught:
        resolve_paths(TOY_SCHEMA, [*write_layers(tmp_path / "layers"), host])
    assert caught.value.key == key


def test_layer_errors(tmp_path: Path) -> None:
    paths = write_layers(tmp_path / "layers")
    twice = tmp_path / "twice"
    twice.mkdir()
    (twice / "a.toml").write_text("[host]\nthreads = 2\n", encoding="utf-8")
    (twice / "b.toml").write_text("[host]\nthreads = 2\n", encoding="utf-8")
    with pytest.raises(LayerError, match="host.threads is set in both"):
        resolve_paths(TOY_SCHEMA, [*paths, twice])

    broken = tmp_path / "broken.toml"
    broken.write_text("[host\n", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    for path in (broken, empty, tmp_path / "absent.toml"):
        with pytest.raises(LayerError):
            load_layer(path)
    for items in (["toy.steps"], ["toy.steps=1", "toy.steps=2"]):
        with pytest.raises(LayerError):
            parse_overrides(TOY_SCHEMA, items)
    with pytest.raises(ConfigValueError):
        parse_overrides(TOY_SCHEMA, ["toy.steps=many"])


def test_errors_name_keys_and_never_quote_values(tmp_path: Path) -> None:
    marker = "Zq9-private-value"
    paths = write_layers(tmp_path / "layers")
    host = tmp_path / "host.toml"
    for text in (f"toy.steps = '{marker}'\n", f"nonesuch = '{marker}'\n"):
        host.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError) as caught:
            resolve_paths(TOY_SCHEMA, [*paths, host])
        assert marker not in str(caught.value)
    with pytest.raises(ConfigError) as caught:
        resolve_paths(TOY_SCHEMA, paths, [f"toy.steps={marker}"])
    assert marker not in str(caught.value)


def test_schema_declarations_are_checked() -> None:
    for fields in (
        {"a.b": "integer"},
        {"A.b": "int"},
        {"a..b": "int"},
        {"": "int"},
        {"a": "int", "a.b": "int"},
        {"a.b": "list[list[int]]"},
    ):
        with pytest.raises(SchemaError):
            Schema(fields)
    with pytest.raises(SchemaError):
        BASE_SCHEMA.extend({"run.stage": "str"})
    with pytest.raises(SchemaError):
        BASE_SCHEMA.extend({"run.stage.name": "str"})
    with pytest.raises(TypeError):
        BASE_SCHEMA.fields["extra"] = "int"  # type: ignore[index]
    assert set(BASE_SCHEMA.fields) == {
        "run.stage",
        "run.dataset_version",
        *(f"seeds.{name}" for name in SEED_NAMES),
    }


# --------------------------------------------------------------------------- toy run


class NetworkDisabledError(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every way of opening a connection or resolving a name fail loudly."""

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise NetworkDisabledError("network access is disabled in this test")

    for name in ("socket", "create_connection", "getaddrinfo", "gethostbyname", "socketpair"):
        monkeypatch.setattr(socket, name, refuse)


class Ticker:
    """A clock that advances one second per reading, so instants are reproducible."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 10, 9, 30, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@dataclass
class ToyRun:
    run_dir: Path
    config: ResolvedConfig
    revision: CodeRevision
    logged: list[Metric]
    checkpoints: dict[int, bytes]


def toy_examples(
    rng: random.Random, weights: tuple[float, ...], count: int
) -> list[tuple[list[float], float]]:
    rows = []
    for _ in range(count):
        x = [rng.uniform(-1.0, 1.0) for _ in weights]
        rows.append((x, sum(w * v for w, v in zip(weights, x, strict=True))))
    return rows


def mean_squared_error(weights: list[float], rows: list[tuple[list[float], float]]) -> float:
    errors = [sum(w * v for w, v in zip(weights, x, strict=True)) - y for x, y in rows]
    return sum(error * error for error in errors) / len(errors)


def run_toy(runs_dir: Path, config: ResolvedConfig, *, fail_at: int | None = None) -> ToyRun:
    """A real, tiny training run driven only by the resolved configuration."""
    true_weights = config.get("toy.true_weights")
    domains = config.get("toy.eval_domains")
    assert isinstance(true_weights, tuple) and isinstance(domains, tuple)
    count = config.get_int("toy.examples")
    rate = config.get_float("toy.learning_rate")
    train = toy_examples(random.Random(config.get_int("seeds.global")), true_weights, count)
    held_out = {
        domain: toy_examples(
            random.Random(config.get_int("seeds.eval_bootstrap") + index), true_weights, 8
        )
        for index, domain in enumerate(domains)
    }
    init = random.Random(config.get_int("seeds.init"))
    order = random.Random(config.get_int("seeds.data_order"))
    weights = [init.uniform(-0.5, 0.5) for _ in true_weights]

    revision = detect_code_revision(REPO)
    hardware = detect_hardware(REPO / "uv.lock", CPU_DEVICE)
    logged: list[Metric] = []
    checkpoints: dict[int, bytes] = {}
    writer = RunWriter.create(
        runs_dir, config, code_revision=revision, hardware=hardware, clock=Ticker()
    )
    try:
        with writer:
            for step in range(1, config.get_int("toy.steps") + 1):
                if step == fail_at:
                    raise RuntimeError("toy run killed")
                x, y = train[order.randrange(count)]
                error = sum(w * v for w, v in zip(weights, x, strict=True)) - y
                weights = [w - rate * 2.0 * error * v for w, v in zip(weights, x, strict=True)]
                logged.append(
                    writer.log_metric(
                        step=step,
                        split="train",
                        name="loss",
                        value=mean_squared_error(weights, train),
                    )
                )
                if step % config.get_int("toy.checkpoint_every") == 0:
                    for domain, rows in held_out.items():
                        logged.append(
                            writer.log_metric(
                                step=step,
                                split="validation",
                                name="loss",
                                value=mean_squared_error(weights, rows),
                                domain=domain,
                            )
                        )
                    directory = writer.checkpoint_dir(step)
                    directory.mkdir()
                    payload = json.dumps({"step": step, "weights": weights}).encode()
                    (directory / "weights.json").write_bytes(payload)
                    writer.register_checkpoint(step)
                    checkpoints[step] = payload
            logged.append(
                writer.log_metric(
                    step=config.get_int("toy.steps"),
                    split="train",
                    name="examples_seen",
                    value=config.get_int("toy.steps"),
                )
            )
            writer.annotate("nondeterminism", {"threads": config.get_int("host.threads")})
    except RuntimeError:
        pass
    return ToyRun(writer.run_dir, config, revision, logged, checkpoints)


@pytest.fixture
def toy(tmp_path: Path, config: ResolvedConfig, no_network: None) -> ToyRun:
    return run_toy(tmp_path / "runs", config)


@pytest.fixture
def loaded(toy: ToyRun, no_network: None) -> LoadedRun:
    return load_run(toy.run_dir)


def test_toy_run_really_trains(toy: ToyRun) -> None:
    losses = [m.value for m in toy.logged if m.split == "train" and m.name == "loss"]
    assert len(losses) == 40
    assert losses[-1] < losses[0] / 10
    assert sorted(toy.checkpoints) == [20, 40]


def test_run_record_reloads_exact_configuration_revision_seeds_and_metrics(
    toy: ToyRun, loaded: LoadedRun
) -> None:
    """The TRM-002 oracle, with network access disabled by the fixtures."""
    record = loaded.record
    # configuration
    assert loaded.config == toy.config
    assert loaded.config.digest == toy.config.digest == record.config_digest
    assert (toy.run_dir / "config.resolved.json").read_bytes() == EXPECTED_CANONICAL
    # code revision
    assert record.code_revision == toy.revision
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert record.code_revision.commit == head and len(head) == 40
    # dataset version, seeds
    assert record.dataset_version == "toy-fixture-v1"
    assert record.label_set_version is None
    assert dict(record.seeds) == {
        "global": 20261110,
        "data_order": 20261111,
        "init": 7,
        "eval_bootstrap": 20261113,
    }
    # metrics: every logged line, bit for bit
    assert loaded.metrics == tuple(toy.logged)
    assert len(loaded.metrics) == 40 + 2 * 2 + 1
    assert loaded.effective_metrics() == loaded.metrics
    assert {m.domain for m in loaded.metrics} == {None, "coding", "conversation"}
    # checkpoints
    assert [c.step for c in loaded.checkpoints] == [20, 40]
    for checkpoint in loaded.checkpoints:
        payload = toy.checkpoints[checkpoint.step]
        assert dict(checkpoint.files) == {"weights.json": hashlib.sha256(payload).hexdigest()}
        assert checkpoint.config_digest == toy.config.digest
        stored = toy.run_dir / "checkpoints" / f"step-{checkpoint.step}" / "weights.json"
        assert stored.read_bytes() == payload
    # identity and status
    assert record.status == "completed"
    assert record.run_id == f"20261110T093001Z-toy-{toy.config.digest[:8]}"
    assert toy.run_dir.name == record.run_id
    assert record.started_at == "2026-11-10T09:30:01.000000Z"
    assert record.ended_at is not None and record.ended_at > record.started_at
    assert record.resumes == 0 and record.parents == ()
    assert record.nondeterminism == {"threads": 1}
    assert record.limits is None and record.device is None


def test_run_record_hardware_facts(loaded: LoadedRun) -> None:
    hardware = loaded.record.hardware
    assert set(hardware) == set(HARDWARE_KEYS)
    assert hardware["cpu_count"] == os.cpu_count()
    ram = hardware["ram_bytes"]
    assert isinstance(ram, int) and ram > 0
    assert hardware["python"] == ".".join(str(part) for part in sys.version_info[:3])
    assert hardware["device_type"] == "cpu" and hardware["gpu_model"] is None
    lock_digest = hashlib.sha256((REPO / "uv.lock").read_bytes()).hexdigest()
    assert hardware["lockfile_digest"] == lock_digest
    libraries = hardware["libraries"]
    assert isinstance(libraries, dict) or hasattr(libraries, "items")
    assert "pytest" in libraries and libraries["pytest"] == pytest.__version__  # type: ignore[operator, index]
    # The host's name is not a hardware fact and is not recorded.
    record_text = (loaded.run_dir / "record.json").read_text(encoding="utf-8")
    assert os.uname().nodename not in record_text


def test_run_record_files_are_open_local_formats(toy: ToyRun) -> None:
    """Clause 11.6: readable with nothing but a JSON parser, one object per line."""
    names = sorted(
        p.relative_to(toy.run_dir).as_posix() for p in toy.run_dir.rglob("*") if p.is_file()
    )
    assert names == [
        "SHA256SUMS",
        "checkpoints/index.jsonl",
        "checkpoints/step-20/weights.json",
        "checkpoints/step-40/weights.json",
        "config.resolved.json",
        "metrics.jsonl",
        "record.json",
    ]
    raw = (toy.run_dir / "metrics.jsonl").read_bytes()
    assert raw.endswith(b"\n")
    lines = raw.decode("utf-8").splitlines()
    assert len(lines) == len(toy.logged)
    for line, metric in zip(lines, toy.logged, strict=True):
        row = json.loads(line)
        assert list(row) == ["t", "step", "split", "name", "value", "domain", "resume"]
        assert row["value"] == metric.value and row["step"] == metric.step
        assert row["resume"] == 0
        datetime.strptime(row["t"], "%Y-%m-%dT%H:%M:%S.%fZ")
    for line in (toy.run_dir / "checkpoints" / "index.jsonl").read_text().splitlines():
        assert set(json.loads(line)) == {"t", "step", "files", "config_digest", "resume"}
    record = json.loads((toy.run_dir / "record.json").read_text(encoding="utf-8"))
    for field in ("config_digest", "code_revision", "dataset_version", "seeds", "hardware"):
        assert record[field] not in (None, "", {})
    # SHA256SUMS is the coreutils format and covers every other file.
    sums: dict[str, str] = {}
    for line in (toy.run_dir / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    assert sorted(sums) == [name for name in names if name != "SHA256SUMS"]
    for name, digest in sums.items():
        assert hashlib.sha256((toy.run_dir / name).read_bytes()).hexdigest() == digest


def test_same_configuration_gives_the_same_metrics_and_a_seed_changes_them(
    tmp_path: Path, layers: list[Path], toy: ToyRun
) -> None:
    again = run_toy(tmp_path / "again", toy.config)
    assert [m.value for m in again.logged] == [m.value for m in toy.logged]
    assert again.run_dir.name == toy.run_dir.name
    other = resolve_paths(TOY_SCHEMA, layers, ["toy.learning_rate=0.1", "seeds.init=8"])
    reseeded = run_toy(tmp_path / "reseeded", other)
    assert [m.value for m in reseeded.logged] != [m.value for m in toy.logged]
    assert reseeded.run_dir.name != toy.run_dir.name
    with pytest.raises(RunRecordError, match="already exists"):
        run_toy(tmp_path / "again", toy.config)


def test_runs_directory_is_ignored_by_git() -> None:
    done = subprocess.run(
        ["git", "-C", str(REPO), "check-ignore", "-q", "runs/20261110T093001Z-toy-0/record.json"]
    )
    assert done.returncode == 0


def test_config_and_runs_packages_need_no_external_service() -> None:
    """Their imports are the standard library and trina itself, with no network client."""
    network = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "xmlrpc", "asyncio"}
    files = sorted((SRC / "config").rglob("*.py")) + sorted((SRC / "runs").rglob("*.py"))
    assert len(files) >= 8
    for file in files:
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0 and node.module is not None
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert roots <= (set(sys.stdlib_module_names) | {"trina"}) - network, (file, roots)


# --------------------------------------------------------------------------- integrity


@pytest.mark.parametrize(
    "name",
    ["metrics.jsonl", "record.json", "checkpoints/index.jsonl", "checkpoints/step-40/weights.json"],
)
def test_a_changed_file_fails_the_reload(toy: ToyRun, name: str) -> None:
    path = toy.run_dir / name
    data = path.read_bytes()
    digit = next(i for i in range(len(data) - 1, -1, -1) if chr(data[i]).isdigit())
    path.write_bytes(
        data[:digit] + (b"1" if data[digit : digit + 1] != b"1" else b"2") + data[digit + 1 :]
    )
    with pytest.raises(RunRecordError):
        load_run(toy.run_dir)


def test_inconsistent_or_incomplete_records_are_refused(tmp_path: Path, toy: ToyRun) -> None:
    def tampered(name: str, old: bytes, new: bytes) -> None:
        path = toy.run_dir / name
        original = path.read_bytes()
        assert old in original
        path.write_bytes(original.replace(old, new, 1))
        try:
            with pytest.raises(RunRecordError):
                load_run(toy.run_dir, verify=False)
        finally:
            path.write_bytes(original)

    # another configuration under the same record; not canonical; identity mismatch
    tampered("config.resolved.json", b'"steps":40', b'"steps":41')
    tampered("config.resolved.json", b'"steps":40', b'"steps": 40')
    tampered("record.json", b'"init": 7', b'"init": 8')
    tampered("record.json", b'"toy-fixture-v1"', b'"toy-fixture-v2"')
    # unknown field, unknown status, unknown version
    tampered("record.json", b'"resumes": 0', b'"resumes": 0, "tracker_url": "x"')
    tampered("record.json", b'"completed"', b'"finished"')
    tampered("record.json", b'"record_version": 1', b'"record_version": 2')
    # malformed lines
    tampered("metrics.jsonl", b'"split":"train"', b'"split":"train","extra":1')
    tampered("metrics.jsonl", b'"step":1,', b'"step":"1",')
    tampered("metrics.jsonl", b'"step":1,', b'"step":1,"step":1,')
    tampered("metrics.jsonl", b"}\n", b"}\n\n")
    # a closed run with an unterminated last line
    path = toy.run_dir / "metrics.jsonl"
    original = path.read_bytes()
    path.write_bytes(original[:-1])
    with pytest.raises(RunRecordError):
        load_run(toy.run_dir, verify=False)
    path.write_bytes(original)

    load_run(toy.run_dir)  # restored: still verifies
    (toy.run_dir / "stray.txt").write_text("x")
    with pytest.raises(RunRecordError, match="files present"):
        load_run(toy.run_dir)
    (toy.run_dir / "stray.txt").unlink()
    (toy.run_dir / "SHA256SUMS").unlink()
    with pytest.raises(RunRecordError):
        load_run(toy.run_dir)
    with pytest.raises(RunRecordError):
        load_run(tmp_path / "no-such-run")


def test_writer_refuses_bad_input(
    tmp_path: Path, config: ResolvedConfig, layers: list[Path]
) -> None:
    revision = CodeRevision("0" * 40, False, None)
    hardware = detect_hardware(REPO / "uv.lock", CPU_DEVICE)

    def create(cfg: ResolvedConfig, **changes: object) -> RunWriter:
        arguments: dict[str, object] = {"code_revision": revision, "hardware": hardware, **changes}
        return RunWriter.create(tmp_path / "runs", cfg, clock=Ticker(), **arguments)  # type: ignore[arg-type]

    with pytest.raises(RunRecordError):
        create(config, hardware={"cpu_count": 1})
    with pytest.raises(RunRecordError):
        create(resolve_paths(TOY_SCHEMA, layers, [*OVERRIDES, "run.stage=../escape"]))
    with pytest.raises(RunRecordError):  # a document that does not pin the run identity
        create(ResolvedConfig({"run": {"stage": "toy"}}))
    assert not (tmp_path / "runs").exists()
    with pytest.raises(RunRecordError):
        detect_hardware(REPO / "uv.lock", {"device_type": "cpu"})
    with pytest.raises(RunRecordError):
        detect_hardware(tmp_path / "no.lock", CPU_DEVICE)
    with pytest.raises(RunRecordError):
        CodeRevision("0" * 40, True, None)

    writer = create(config)
    for bad in (
        {"step": -1},
        {"step": True},
        {"step": 1.0},
        {"split": ""},
        {"name": ""},
        {"domain": ""},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": True},
        {"value": "0.5"},
    ):
        arguments = {"step": 1, "split": "train", "name": "loss", "value": 0.5, **bad}
        with pytest.raises(RunRecordError):
            writer.log_metric(**arguments)  # type: ignore[arg-type]
    assert (writer.run_dir / "metrics.jsonl").read_bytes() == b""
    with pytest.raises(RunRecordError):
        writer.register_checkpoint(5)  # no such directory
    with pytest.raises(RunRecordError):
        writer.annotate("notes", {"a": 1})
    for status in ("running", "finished"):
        with pytest.raises(RunRecordError):
            writer.close(status)
    assert writer.close("failing_baseline").status == "failing_baseline"
    assert "failing_baseline" in STATUSES
    with pytest.raises(RunRecordError, match="closed"):
        writer.log_metric(step=1, split="train", name="loss", value=0.5)
    with pytest.raises(RunRecordError, match="closed"):
        writer.close()
    assert load_run(writer.run_dir).record.status == "failing_baseline"


# --------------------------------------------------------------------------- failure and resume


def test_failed_run_is_recorded_as_failed(tmp_path: Path, config: ResolvedConfig) -> None:
    killed = run_toy(tmp_path / "runs", config, fail_at=25)
    loaded = load_run(killed.run_dir)
    assert loaded.record.status == "failed"
    assert loaded.metrics == tuple(killed.logged)
    assert [c.step for c in loaded.checkpoints] == [20]


def test_killed_run_reloads_and_resumes(
    tmp_path: Path, config: ResolvedConfig, layers: list[Path]
) -> None:
    revision = CodeRevision("0" * 40, True, "f" * 64)
    hardware = detect_hardware(REPO / "uv.lock", CPU_DEVICE)
    clock = Ticker()
    writer = RunWriter.create(
        tmp_path / "runs",
        config,
        code_revision=revision,
        hardware=hardware,
        parents=("p-1",),
        clock=clock,
    )
    first = [
        writer.log_metric(step=s, split="train", name="loss", value=1.0 / s) for s in (1, 2, 3)
    ]
    # A kill mid-write: the process is gone, the status is still running, a line is cut.
    with (writer.run_dir / "metrics.jsonl").open("ab") as handle:
        handle.write(b'{"t":"2026-11-10T09:30:09.000000Z","step":4,"spl')

    crashed = load_run(writer.run_dir)
    assert crashed.record.status == "running" and crashed.record.ended_at is None
    assert crashed.metrics == tuple(first)
    assert crashed.record.parents == ("p-1",)
    assert not (writer.run_dir / "SHA256SUMS").exists()

    other = resolve_paths(TOY_SCHEMA, layers, ["toy.learning_rate=0.2"])
    with pytest.raises(RunRecordError, match="digest differs"):
        RunWriter.resume(writer.run_dir, other, clock=clock)

    with RunWriter.resume(writer.run_dir, config, clock=clock) as resumed:
        # restored from the checkpoint of step 2: step 3 is logged again
        second = [
            resumed.log_metric(step=s, split="train", name="loss", value=2.0 / s) for s in (3, 4)
        ]
    final = load_run(writer.run_dir)
    assert final.record.status == "completed" and final.record.resumes == 1
    assert final.record.code_revision == revision
    assert final.metrics == (*first, *second)
    assert [m.resume for m in final.metrics] == [0, 0, 0, 1, 1]
    effective = final.effective_metrics()
    assert [(m.step, m.value, m.resume) for m in effective] == [
        (1, 1.0, 0),
        (2, 0.5, 0),
        (3, 2.0 / 3, 1),
        (4, 0.5, 1),
    ]


# --------------------------------------------------------------------------- code revision


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Iterator[Path]:
    repo = tmp_path / "scratch"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text("runs/\n")
    (repo / "model.py").write_text("WEIGHT = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "first")
    yield repo


def test_code_revision_names_the_commit_and_digests_a_dirty_tree(
    scratch_repo: Path, tmp_path: Path
) -> None:
    clean = detect_code_revision(scratch_repo)
    assert clean == CodeRevision(git(scratch_repo, "rev-parse", "HEAD"), False, None)

    # Ignored output, such as the run records themselves, does not make the tree dirty.
    (scratch_repo / "runs").mkdir()
    (scratch_repo / "runs" / "record.json").write_text("{}")
    assert detect_code_revision(scratch_repo) == clean

    (scratch_repo / "model.py").write_text("WEIGHT = 2\n")
    edited = detect_code_revision(scratch_repo)
    assert edited.commit == clean.commit and edited.dirty
    assert edited.dirty_digest is not None and len(edited.dirty_digest) == 64
    assert detect_code_revision(scratch_repo) == edited  # stable

    (scratch_repo / "model.py").write_text("WEIGHT = 3\n")
    edited_again = detect_code_revision(scratch_repo)
    assert edited_again.dirty_digest != edited.dirty_digest

    (scratch_repo / "new.py").write_text("A = 1\n")
    untracked = detect_code_revision(scratch_repo)
    (scratch_repo / "new.py").write_text("A = 2\n")
    assert (
        len(
            {
                edited_again.dirty_digest,
                untracked.dirty_digest,
                detect_code_revision(scratch_repo).dirty_digest,
            }
        )
        == 3
    )

    assert CodeRevision.from_json(json.loads(json.dumps(untracked.to_json()))) == untracked

    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    env_dir = os.environ.get("GIT_CEILING_DIRECTORIES")
    os.environ["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    try:
        with pytest.raises(RunRecordError, match="sync manifest"):
            detect_code_revision(not_a_repo)
    finally:
        if env_dir is None:
            del os.environ["GIT_CEILING_DIRECTORIES"]
        else:
            os.environ["GIT_CEILING_DIRECTORIES"] = env_dir


def test_layer_from_table_resolves_like_a_file(config: ResolvedConfig) -> None:
    table = json.loads(EXPECTED_CANONICAL)
    assert resolve(TOY_SCHEMA, [Layer.from_table("inline", table)]) == config
