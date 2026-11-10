"""Task 0.4: secret loading, redaction, residual-secret audit and artifact scanner.

Cases TRM-003 and TRM-004. Every credential here is synthetic: the redaction corpus
is tests/fixtures/security/synthetic_secrets.toml, whose values are split by a join
marker, and the values used elsewhere are assembled at run time, so that no credential
pattern is written to a tracked file. No test
reads a real credential: every loader gets an explicit temporary secrets directory,
and every subprocess gets TRINA_SECRETS_DIR pointing at one.
"""

from __future__ import annotations

import copy
import gzip
import io
import json
import logging
import os
import pickle
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trina.security import (
    KINDS,
    SECRET_PLACEHOLDER,
    AuditRecord,
    CredentialFileError,
    MissingSecretError,
    RedactedRecord,
    RedactedText,
    RedactionError,
    ResidualSecretError,
    Secret,
    SecretLogFilter,
    SecretsLoader,
    audit_content,
    audit_dataset,
    credential_patterns,
    empty_counts,
    install_guards,
    install_log_guard,
    placeholder,
    redact_record,
    redact_text,
    scan_artifacts,
    scan_file,
    scanner,
    sum_counts,
)
from trina.security.loader import MIN_COMPARABLE, CredentialFormatError

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "security" / "synthetic_secrets.toml"
HYGIENE = REPO / "scripts" / "check_hygiene.py"
PYTHON = sys.executable

# The fixture splits each credential shape with a join marker; the whole value is
# assembled here, in memory only.
JOIN = "{+}"
CORPUS: dict[str, Any] = tomllib.loads(FIXTURE.read_text(encoding="utf-8").replace(JOIN, ""))
CASES: list[dict[str, Any]] = CORPUS["case"]
CLEAN: list[dict[str, Any]] = CORPUS["clean"]

# Synthetic values, assembled so that this file holds no credential pattern.
BODY = "a1B2c3D4" * 4
VALUE_ENV = "synthetic-env-" + BODY
VALUE_FILE = "synthetic-file-" + BODY
VALUE_SSH = "synthetic-ssh-" + BODY
PROVIDER_KEY = "sk-" + "SYNTH" + BODY
CHAT_KEY = "xoxb-" + "000000000000-" + BODY
IGNORES = ".env\n.ssh.creds\ndata/\nruns/\n"
# 32 distinct characters: 5 bits per character.
HIGH_ENTROPY = "Zq7Lm2Xv9Kp4Rt8Wn3Yc6Bd1Hf5Jg0Sa"


def write_secret_file(path: Path, text: str, mode: int = 0o600) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "secrets"
    directory.mkdir()
    write_secret_file(
        directory / ".env",
        "# synthetic\n"
        f"FILE_ONLY={VALUE_FILE}\n"
        f"export SHADOWED='{VALUE_FILE}-shadowed'\n"
        f'QUOTED="{VALUE_FILE} with \\"quotes\\"" # trailing comment\n'
        f"BOTH_FILES={VALUE_FILE}-first\n"
        "SHORT=abc\n",
    )
    write_secret_file(
        directory / ".ssh.creds",
        f"GPU_PASSWORD={VALUE_SSH}\nBOTH_FILES={VALUE_SSH}-second\n",
    )
    return directory


@pytest.fixture
def loader(secrets_dir: Path) -> SecretsLoader:
    environ = {"ENV_ONLY": VALUE_ENV, "SHADOWED": VALUE_ENV + "-wins"}
    return SecretsLoader(environ=environ, secrets_dir=secrets_dir)


def subprocess_env(secrets: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "TRINA_SECRETS_DIR"}
    env["TRINA_SECRETS_DIR"] = str(secrets)
    env.update(extra)
    return env


def run(
    *command: str | Path, cwd: Path = REPO, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


def output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Secrets loader (clauses 3.1, 3.5)
# ---------------------------------------------------------------------------


def test_loader_reads_environment_and_both_credential_files(loader: SecretsLoader) -> None:
    assert loader.get("ENV_ONLY").reveal() == VALUE_ENV
    assert loader.get("FILE_ONLY").reveal() == VALUE_FILE
    assert loader.get("GPU_PASSWORD").reveal() == VALUE_SSH
    assert loader.get("QUOTED").reveal() == f'{VALUE_FILE} with "quotes"'
    # The environment wins over the files, and .env over .ssh.creds.
    assert loader.get("SHADOWED").reveal() == VALUE_ENV + "-wins"
    assert loader.get("BOTH_FILES").reveal() == VALUE_FILE + "-first"
    assert loader.names() == [
        "BOTH_FILES",
        "FILE_ONLY",
        "GPU_PASSWORD",
        "QUOTED",
        "SHADOWED",
        "SHORT",
    ]
    assert loader.get_optional("ABSENT") is None


def test_missing_secret_names_the_variable_and_no_value(loader: SecretsLoader) -> None:
    with pytest.raises(MissingSecretError) as raised:
        loader.get("ABSENT")
    message = str(raised.value)
    assert "ABSENT" in message
    assert "synthetic" not in message


def test_secrets_dir_comes_from_the_environment_variable(secrets_dir: Path) -> None:
    found = SecretsLoader(environ={"TRINA_SECRETS_DIR": str(secrets_dir)})
    assert found.get("FILE_ONLY").reveal() == VALUE_FILE
    assert found.secrets_dir == secrets_dir
    with pytest.raises(CredentialFileError):
        SecretsLoader(environ={"TRINA_SECRETS_DIR": str(secrets_dir / "absent")}).get("X")


def test_secrets_dir_defaults_to_the_main_repository_root(tmp_path: Path) -> None:
    """In a task worktree the credential files are absent; the main root has them."""
    main = tmp_path / "main"
    main.mkdir()
    git = ("git", "-c", "user.name=t", "-c", "user.email=t@example.test")
    assert run(*git, "init", "-q", "-b", "main", cwd=main).returncode == 0
    (main / ".gitignore").write_text(IGNORES)
    assert run(*git, "add", ".gitignore", cwd=main).returncode == 0
    assert run(*git, "commit", "-q", "-m", "init", cwd=main).returncode == 0
    worktree = tmp_path / "worktree"
    assert run(*git, "worktree", "add", "-q", worktree, "-b", "task", cwd=main).returncode == 0
    write_secret_file(main / ".env", f"FILE_ONLY={VALUE_FILE}\n")
    assert not (worktree / ".env").exists()
    code = (
        "from trina.security import SecretsLoader\n"
        "loader = SecretsLoader()\n"
        "print(loader.secrets_dir)\n"
        f"print(loader.get('FILE_ONLY').reveal() == {VALUE_FILE!r})\n"
        "print(loader.get('FILE_ONLY'))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "TRINA_SECRETS_DIR"}
    result = run(PYTHON, "-c", code, cwd=worktree, env=env)
    assert result.returncode == 0, output(result)
    assert result.stdout.splitlines() == [str(main), "True", SECRET_PLACEHOLDER]


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o400, 0o700])
def test_loader_refuses_a_credential_file_that_is_not_mode_600(
    secrets_dir: Path, mode: int
) -> None:
    (secrets_dir / ".env").chmod(mode)
    refused = SecretsLoader(environ={}, secrets_dir=secrets_dir)
    with pytest.raises(CredentialFileError) as raised:
        refused.get("FILE_ONLY")
    assert f"mode is {mode:03o}, not 600" in str(raised.value)
    assert "synthetic" not in str(raised.value)


def test_malformed_credential_file_error_never_quotes_the_line(secrets_dir: Path) -> None:
    write_secret_file(secrets_dir / ".ssh.creds", f"host {VALUE_SSH}\n")
    partial = SecretsLoader(environ={}, secrets_dir=secrets_dir)
    # The other file still serves its names; the unparsed one is reported.
    assert partial.get("FILE_ONLY").reveal() == VALUE_FILE
    (error,) = partial.unparsed()
    assert isinstance(error, CredentialFormatError)
    assert str(error).endswith(".ssh.creds: line 1 is not NAME=VALUE")
    with pytest.raises(CredentialFormatError) as raised:
        partial.get("GPU_PASSWORD")
    assert VALUE_SSH not in str(raised.value)
    assert VALUE_SSH not in repr(raised.value)


def test_secret_is_opaque_everywhere_it_can_be_shown(loader: SecretsLoader) -> None:
    secret = loader.get("FILE_ONLY")
    assert isinstance(secret, Secret)
    shown = [
        repr(secret),
        str(secret),
        f"{secret}",
        f"{secret!r}",
        f"{secret:>40}",
        "%s %r" % (secret, secret),  # noqa: UP031
        format(secret, "s"),
        repr([secret, {"key": secret}]),
        json.dumps({"key": secret}, default=str),
        repr(copy.deepcopy({"key": secret})),
    ]
    for text in shown:
        assert VALUE_FILE not in text
        assert SECRET_PLACEHOLDER in text
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    with pytest.raises(TypeError):
        json.dumps({"key": secret})
    assert not hasattr(secret, "__dict__")
    assert secret == loader.get("FILE_ONLY")
    assert secret != loader.get("GPU_PASSWORD")
    assert secret.name == "FILE_ONLY"


def test_loader_never_writes(secrets_dir: Path, loader: SecretsLoader) -> None:
    def snapshot() -> dict[str, tuple[bytes, int, int]]:
        return {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
            for path in sorted(secrets_dir.iterdir())
        }

    before = snapshot()
    loader.load_all()
    loader.get("FILE_ONLY").reveal()
    loader.scrub(f"text with {VALUE_FILE}")
    list(loader.occurrences(VALUE_SSH))
    assert snapshot() == before


def test_scrub_replaces_held_values_and_their_encodings(loader: SecretsLoader) -> None:
    quoted = loader.get("QUOTED").reveal()
    loader.load_all()
    text = f"a={VALUE_FILE} b={VALUE_SSH} c={json.dumps(quoted)} d=abc"
    scrubbed = loader.scrub(text)
    assert "synthetic" not in scrubbed
    assert scrubbed.count(SECRET_PLACEHOLDER) == 3
    # A value too short to compare is left alone and reported by name.
    assert len("abc") < MIN_COMPARABLE
    assert scrubbed.endswith("d=abc")
    assert loader.uncomparable_names() == ["SHORT"]
    assert sorted(name for name, _ in loader.occurrences(f"x {VALUE_SSH} y")) == ["GPU_PASSWORD"]


# ---------------------------------------------------------------------------
# A secret through logging and exception messages (clause 3.1, TRM-003)
# ---------------------------------------------------------------------------


@pytest.fixture
def guarded_log(loader: SecretsLoader) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("trina.test_0_4.child")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = True
    uninstall = install_log_guard(loader)
    try:
        yield logger, stream
    finally:
        uninstall()
        logger.removeHandler(handler)


def test_secret_passed_through_logging_does_not_appear(
    loader: SecretsLoader,
    guarded_log: tuple[logging.Logger, io.StringIO],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger, stream = guarded_log
    secret = loader.get("FILE_ONLY")
    raw = secret.reveal()
    with caplog.at_level(logging.DEBUG):
        logger.info("opaque value: %s / %r", secret, secret)
        logger.info("raw value in the message: " + raw)
        logger.warning("raw value in the arguments: %s and %s", raw, {"header": raw})
        try:
            raise RuntimeError(f"client failed with key {raw}")
        except RuntimeError:
            logger.exception("raw value in a logged exception")
        print(f"printed: {secret}")
    captured = capsys.readouterr()
    everything = "\n".join(
        [stream.getvalue(), caplog.text, captured.out, captured.err]
        + [record.getMessage() for record in caplog.records]
        + [record.exc_text or "" for record in caplog.records]
    )
    assert raw not in everything
    assert "synthetic" not in everything
    assert all(record.exc_info is None for record in caplog.records)
    logged = stream.getvalue()
    assert logged.count(SECRET_PLACEHOLDER) >= 6
    assert "RuntimeError: client failed with key " + SECRET_PLACEHOLDER in logged
    # The guard is global: it was installed once and covers a child logger.
    assert "trina.test_0_4.child" in logged


def test_log_filter_scrubs_extra_fields(loader: SecretsLoader) -> None:
    raw = loader.get("GPU_PASSWORD").reveal()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s host=%(host)s"))
    handler.addFilter(SecretLogFilter(loader))
    logger = logging.getLogger("trina.test_0_4.extra")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.error("connect with %s", raw, extra={"host": raw})
    finally:
        logger.removeHandler(handler)
    assert raw not in stream.getvalue()
    assert stream.getvalue().count(SECRET_PLACEHOLDER) == 2


def test_guards_can_be_removed(loader: SecretsLoader) -> None:
    factory, hook = logging.getLogRecordFactory(), sys.excepthook
    uninstall = install_guards(loader)
    assert logging.getLogRecordFactory() is not factory
    assert sys.excepthook is not hook
    uninstall()
    assert logging.getLogRecordFactory() is factory
    assert sys.excepthook is hook


def test_secret_in_an_exception_message_does_not_appear(loader: SecretsLoader) -> None:
    secret = loader.get("FILE_ONLY")
    with pytest.raises(ValueError) as raised:
        raise ValueError(f"request rejected for key {secret} ({secret!r})")
    assert secret.reveal() not in str(raised.value)
    assert str(raised.value).count(SECRET_PLACEHOLDER) == 2


def test_secret_does_not_reach_process_output(secrets_dir: Path) -> None:
    """A real process: logging to stderr, then an uncaught exception holding the value."""
    code = (
        "import logging, sys\n"
        "from trina.security import SecretsLoader, install_guards\n"
        "loader = SecretsLoader()\n"
        "install_guards(loader)\n"
        "logging.basicConfig(level=logging.INFO, stream=sys.stderr)\n"
        "secret = loader.get('FILE_ONLY')\n"
        "raw = secret.reveal()\n"
        "logging.getLogger('worker').info('calling with %s and %s', secret, raw)\n"
        "print('stdout sees', secret)\n"
        "try:\n"
        "    raise KeyError(raw)\n"
        "except KeyError:\n"
        "    logging.getLogger('worker').exception('lookup failed')\n"
        "raise RuntimeError('upstream echoed the request: Authorization ' + raw)\n"
    )
    result = run(PYTHON, "-c", code, env=subprocess_env(secrets_dir))
    assert result.returncode == 1
    text = output(result)
    assert VALUE_FILE not in text
    assert "synthetic" not in text
    assert "RuntimeError: upstream echoed the request: Authorization " + SECRET_PLACEHOLDER in text
    assert "Traceback (most recent call last)" in text
    assert f"calling with {SECRET_PLACEHOLDER} and {SECRET_PLACEHOLDER}" in text
    assert f"stdout sees {SECRET_PLACEHOLDER}" in result.stdout


# ---------------------------------------------------------------------------
# Redaction (clause 3.2, TRM-004)
# ---------------------------------------------------------------------------


def nonzero(counts: dict[str, int]) -> dict[str, int]:
    return {kind: value for kind, value in counts.items() if value}


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_every_pattern_is_redacted_with_the_right_count(case: dict[str, Any]) -> None:
    redacted = redact_text(case["text"])
    assert redacted.text == case["expect"]
    assert nonzero(redacted.counts) == case["counts"]
    assert set(redacted.counts) == set(KINDS)
    for fragment in case["gone"]:
        assert fragment not in redacted.text
    # Idempotent: redacting the result changes nothing and counts nothing.
    again = redact_text(redacted.text)
    assert again.text == redacted.text
    assert nonzero(again.counts) == {}


def test_the_corpus_covers_every_kind_of_the_catalogue() -> None:
    covered = {kind for case in CASES for kind in case["counts"]}
    # Headers are structured data: test_record_* below covers them.
    assert covered == set(KINDS) - {"header"}
    catalogue = {kind for kind, _ in credential_patterns()}
    assert catalogue == {"api_key", "cloud_token", "repo_token", "bearer", "private_key"}


@pytest.mark.parametrize("clean", CLEAN, ids=[str(i) for i in range(len(CLEAN))])
def test_ordinary_text_is_left_alone(clean: dict[str, Any]) -> None:
    redacted = redact_text(clean["text"])
    assert redacted.text == clean["text"]
    assert nonzero(redacted.counts) == {}


def test_counts_add_up_over_a_mixed_text() -> None:
    text = "\n".join(case["text"] for case in CASES)
    expected = sum_counts(case["counts"] for case in CASES)
    redacted = redact_text(text)
    assert redacted.counts == expected
    assert sum(expected.values()) == 40
    for case in CASES:
        for fragment in case["gone"]:
            assert fragment not in redacted.text


@pytest.mark.parametrize(
    "unit",
    ["KEY_", "token-", "-----BEGIN " + "PRIVATE KEY----- ", "://a:b", "a1B2", "ab:", "1.2."],
)
def test_redaction_time_is_linear_in_the_text(unit: str) -> None:
    """A long unbroken run must not send a pattern quadratic: records can be megabytes."""
    text = (unit * 200_000)[:200_000]
    started = time.perf_counter()
    redact_text(text)
    # Linear takes well under a second here; the quadratic forms took minutes.
    assert time.perf_counter() - started < 20


def raw_record() -> dict[str, object]:
    return {
        "id": "rec-1",
        "headers": {
            "Authorization": "Bearer " + BODY,
            "User-Agent": "agent/1.0",
            "X-Forwarded-For": "203.0.113.7",
            "X-Unknown-New-Header": "anything",
        },
        "request": {
            "request_headers": [["x-device-id", "synthetic-device"], ["accept", "*/*"]],
            "client_ip": "198.51.100.23",
            "installationId": "synthetic-install-0001",
            "body": {
                "model": "m",
                "api_key": "short",
                "max_tokens": 4096,
                "messages": [
                    {"role": "user", "content": f"my key is {PROVIDER_KEY}, host 192.0.2.1"},
                    {"role": "tool", "content": "password: synth3tic7 on 02:00:5e:10:00:ff"},
                ],
            },
        },
        "response": {"responseHeaders": {"set-cookie": "sid=" + BODY}, "usage": {"tokens": 12}},
        "machine_id": None,
        "ok": True,
        "score": 0.5,
    }


def test_record_redaction_drops_headers_addresses_and_identifiers() -> None:
    record = redact_record(raw_record())
    assert isinstance(record, RedactedRecord)
    assert record.data == {
        "id": "rec-1",
        "request": {
            "client_ip": "[DROPPED:ip_address]",
            "installationId": "[DROPPED:installation_id]",
            "body": {
                "model": "m",
                "api_key": "[REDACTED:assignment]",
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": "my key is [REDACTED:api_key], host [DROPPED:ip_address]",
                    },
                    {
                        "role": "tool",
                        "content": "password: [REDACTED:assignment] on [DROPPED:installation_id]",
                    },
                ],
            },
        },
        "response": {"usage": {"tokens": 12}},
        "machine_id": None,
        "ok": True,
        "score": 0.5,
    }
    assert record.counts == {
        **empty_counts(),
        # Every header field is dropped, name and value: 4 + 2 + 1, the unknown one too.
        "header": 7,
        "ip_address": 2,
        "installation_id": 2,
        "assignment": 2,
        "api_key": 1,
    }
    text = record.to_json()
    for gone in ("headers", "Headers", "User-Agent", "X-Unknown", "203.0.113.7", BODY, "synth"):
        assert gone not in text
    assert json.loads(text) == record.data


def test_record_redaction_is_idempotent_and_does_not_touch_its_input() -> None:
    source = raw_record()
    before = copy.deepcopy(source)
    first = redact_record(source)
    assert source == before
    second = redact_record(first.data)
    assert second.data == first.data
    assert nonzero(second.counts) == {}
    # The redacted content cannot be changed through the object.
    first.data["id"] = "changed"
    assert first.data["id"] == "rec-1"


def test_only_redaction_creates_the_redacted_types() -> None:
    with pytest.raises(TypeError):
        RedactedRecord(object(), {"text": PROVIDER_KEY}, empty_counts())
    with pytest.raises(TypeError):
        RedactedText(object(), PROVIDER_KEY, empty_counts())
    with pytest.raises(TypeError):
        RedactedRecord({"text": PROVIDER_KEY})  # type: ignore[call-arg]


def test_a_value_that_is_not_json_data_is_refused() -> None:
    with pytest.raises(RedactionError) as raised:
        redact_record({"a": {"b": [1, b"bytes"]}})
    assert "a.b[1]" in str(raised.value)
    with pytest.raises(RedactionError):
        redact_record({1: "x"})  # type: ignore[dict-item]
    with pytest.raises(RedactionError):
        redact_text(b"bytes")  # type: ignore[arg-type]


def test_placeholders_are_typed_and_carry_nothing_of_the_value() -> None:
    assert placeholder("api_key") == "[REDACTED:api_key]"
    assert placeholder("ip_address") == "[DROPPED:ip_address]"
    with pytest.raises(ValueError):
        placeholder("header")
    one = redact_text(f"{PROVIDER_KEY} and 10.0.0.1").text
    other = redact_text(f"{PROVIDER_KEY}XYZ and 10.9.9.9").text
    assert one == other == "[REDACTED:api_key] and [DROPPED:ip_address]"


# ---------------------------------------------------------------------------
# Residual-secret audit (clause 3.3, TRM-004)
# ---------------------------------------------------------------------------


def clean_dataset(size: int, strata: int = 4) -> list[AuditRecord]:
    records = []
    for index in range(size):
        content = redact_record(
            {"id": index, "text": f"note {index}: host 10.0.0.{index % 250} said hello"}
        )
        records.append(AuditRecord(f"rec-{index:05d}", f"source-{index % strata}", content))
    return records


def test_audit_passes_on_a_redacted_dataset() -> None:
    report = audit_dataset("ds@1", clean_dataset(60), seed=7, sample_size=None)
    assert report.passed
    assert (report.population, report.sample_size, report.full_scan) == (60, 60, True)
    assert (report.high, report.low) == (0, 0)
    report.require_pass()


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_audit_catches_every_planted_kind(case: dict[str, Any]) -> None:
    records = clean_dataset(30)
    records[17] = AuditRecord("planted", "source-1", {"text": "ok", "nested": [case["text"]]})
    report = audit_dataset("ds@1", records, seed=7, sample_size=None)
    assert not report.passed
    with pytest.raises(ResidualSecretError) as raised:
        report.require_pass()
    high = [f for f in report.findings if f.confidence == "high"]
    assert {f.record_id for f in high} == {"planted"}
    assert {f.path for f in high} == {"nested[0]"}
    assert set(case["counts"]) <= {f.kind for f in high if f.detector == "catalogue"}
    # Neither the error nor the report holds the matched text.
    told = str(raised.value) + json.dumps(report.to_dict())
    for fragment in case["gone"]:
        assert fragment not in told
    assert "ds@1" in str(raised.value)


def test_audit_stricter_detectors() -> None:
    def detectors(text: str) -> set[tuple[str, str]]:
        return {(f.detector, f.confidence) for f in audit_content("r", {"t": text})}

    # A high-entropy string next to a credential-like name, in a form redaction misses.
    assert detectors(f"the password is\n{HIGH_ENTROPY}") == {("entropy", "high")}
    # A hexadecimal string next to such a name is a digest more often: low.
    assert detectors("secret digest " + "0a1b2c3d4e5f6789" * 2) == {("entropy", "low")}
    # No credential-like name nearby: not a finding.
    assert detectors(f"build artifact {HIGH_ENTROPY}") == set()
    # A known prefix with a tail too short for the catalogue: low.
    assert detectors("see " + "ghp_" + "abcd1234efgh") == {("key_prefix", "low")}
    # A key marker that is not a whole block.
    assert ("private_key_marker", "high") in detectors(
        "ends with -----END RSA " + "PRIVATE KEY-----"
    )
    # A surviving header container.
    kinds = {(f.detector, f.kind) for f in audit_content("r", {"request_headers": {"a": "b"}})}
    assert kinds == {("structure", "header")}


def test_low_confidence_matches_are_listed_and_do_not_fail() -> None:
    records = clean_dataset(10)
    records.append(AuditRecord("weak", "source-0", "see " + "ghp_" + "abcd1234efgh"))
    report = audit_dataset("ds@1", records, seed=1, sample_size=None)
    assert report.passed
    assert (report.high, report.low) == (0, 1)
    assert [f.record_id for f in report.findings] == ["weak"]
    assert report.to_dict()["counts"]["key_prefix"] == {"high": 0, "low": 1}  # type: ignore[index]


def test_audit_compares_with_the_values_the_loader_holds(loader: SecretsLoader) -> None:
    loader.load_all()
    # No recognisable shape, so redaction leaves it; only the comparison finds it.
    leaked = redact_record({"text": f"she typed {VALUE_SSH} into the chat"})
    assert VALUE_SSH in leaked.to_json()
    records = [*clean_dataset(5), AuditRecord("leak", "source-0", leaked)]
    assert audit_dataset("ds@1", records, seed=1, sample_size=None).passed
    report = audit_dataset("ds@1", records, seed=1, sample_size=None, loader=loader)
    assert not report.passed
    assert report.counts["held_secret"] == {"high": 1, "low": 0}
    assert VALUE_SSH not in json.dumps(report.to_dict())
    assert VALUE_SSH not in str(ResidualSecretError(report))


def test_audit_sample_is_seeded_stratified_and_recorded() -> None:
    records = clean_dataset(400, strata=4) + [
        AuditRecord(f"rare-{i}", "rare-source", redact_record({"text": "hello"})) for i in range(3)
    ]
    report = audit_dataset("ds@1", records, seed=11, sample_size=40)
    assert (report.population, report.sample_size, report.seed) == (403, 40, 11)
    assert not report.full_scan
    # Proportional, and a small stratum is still sampled.
    assert report.strata == {
        "source-0": (100, 9),
        "source-1": (100, 10),
        "source-2": (100, 10),
        "source-3": (100, 10),
        "rare-source": (3, 1),
    }
    assert report.to_dict()["sample_size"] == 40

    def sampled(seed: int, order: list[AuditRecord]) -> set[str]:
        # Plant a low-confidence marker in every record: findings then list the sample.
        marked = [
            AuditRecord(r.record_id, r.stratum, "see " + "ghp_" + "abcd1234efgh") for r in order
        ]
        report = audit_dataset("ds@1", marked, seed=seed, sample_size=40)
        return {finding.record_id for finding in report.findings}

    first = sampled(11, records)
    assert len(first) == 40
    # Same seed, another order: the same sample. Another seed: another sample.
    assert sampled(11, list(reversed(records))) == first
    assert sampled(12, records) != first


def test_small_dataset_is_scanned_in_full_and_a_planted_secret_is_caught() -> None:
    records = clean_dataset(25)
    records[3] = AuditRecord("planted", "source-3", {"text": f"key {PROVIDER_KEY}"})
    report = audit_dataset("ds@1", records, seed=5, sample_size=1000)
    assert report.full_scan and report.sample_size == 25
    with pytest.raises(ResidualSecretError):
        report.require_pass()
    with pytest.raises(ValueError):
        audit_dataset("ds@1", records, seed=5, sample_size=0)


# ---------------------------------------------------------------------------
# Artifact scanner and the gates (clause 3.1, TRM-003)
# ---------------------------------------------------------------------------


def artifact_tree(root: Path) -> Path:
    (root / "data" / "ds").mkdir(parents=True)
    (root / "runs" / "r1").mkdir(parents=True)
    (root / "data" / "ds" / "shard-0.jsonl").write_text(
        json.dumps({"text": "host [DROPPED:ip_address] said [REDACTED:api_key]"}) + "\n"
    )
    (root / "runs" / "r1" / "train.log").write_text("epoch 1 loss 0.5\nepoch 2 loss 0.4\n")
    (root / "runs" / "r1" / "weights.bin").write_bytes(bytes(range(256)) * 64)
    return root


def scan_command(root: Path, secrets: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(PYTHON, "-m", "trina.security", "--root", root, *args, env=subprocess_env(secrets))


def test_scanner_command_passes_on_clean_artifacts(tmp_path: Path, secrets_dir: Path) -> None:
    root = artifact_tree(tmp_path / "tree")
    result = scan_command(root, secrets_dir)
    assert result.returncode == 0, output(result)
    assert "artifacts: OK (3 file(s) scanned" in result.stdout
    assert "held value(s) compared" in result.stdout
    # No artifact directory at all is a clean tree, not an error.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert scan_command(empty, secrets_dir).returncode == 0


def test_scanner_command_fails_on_a_planted_credential(tmp_path: Path, secrets_dir: Path) -> None:
    root = artifact_tree(tmp_path / "tree")
    with (root / "runs" / "r1" / "train.log").open("a") as handle:
        handle.write(f"request failed: key {PROVIDER_KEY}\n")
    result = scan_command(root, secrets_dir)
    assert result.returncode == 1
    assert "ARTIFACT FAIL credential-pattern runs/r1/train.log:3: api_key [" in result.stdout
    assert "artifacts: FAIL (1 finding(s) in 3 file(s))" in result.stdout
    assert PROVIDER_KEY not in output(result)
    assert BODY not in output(result)


def test_scanner_command_fails_on_a_loaded_secret_value(tmp_path: Path, secrets_dir: Path) -> None:
    """A value with no credential shape is found by comparison with the loader."""
    root = artifact_tree(tmp_path / "tree")
    (root / "data" / "ds" / "shard-1.jsonl").write_text(
        "first\n" + json.dumps({"text": f"typed {VALUE_SSH} here"}) + "\n"
    )
    result = scan_command(root, secrets_dir)
    assert result.returncode == 1
    assert (
        "ARTIFACT FAIL held-secret data/ds/shard-1.jsonl:2: value of GPU_PASSWORD" in result.stdout
    )
    assert "synthetic" not in output(result)
    # Patterns only: the same tree passes without the value comparison.
    patterns_only = scan_command(root, secrets_dir, "--no-secrets")
    assert patterns_only.returncode == 0, output(patterns_only)
    assert "value comparison off" in patterns_only.stdout


def test_scanner_reads_compressed_files_and_never_passes_an_unread_one(tmp_path: Path) -> None:
    root = artifact_tree(tmp_path / "tree")
    shard = root / "data" / "ds" / "shard-2.jsonl.gz"
    with gzip.open(shard, "wt") as handle:
        handle.write("line one\n" + json.dumps({"text": CHAT_KEY}) + "\n")
    (root / "data" / "ds" / "table.parquet").write_bytes(b"PAR1" + bytes(64))
    (root / "data" / "ds" / "broken.jsonl.zst").write_bytes(b"not a zstd frame")
    result = scan_artifacts(root)
    found = {(f.check, f.location) for f in result.findings}
    assert found == {
        ("credential-pattern", "data/ds/shard-2.jsonl.gz:2"),
        ("unscanned", "data/ds/table.parquet"),
        ("unscanned", "data/ds/broken.jsonl.zst"),
    }
    assert not result.passed
    assert result.files == 6


def test_scanner_finds_a_match_across_a_chunk_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loader: SecretsLoader
) -> None:
    monkeypatch.setattr(scanner, "CHUNK_BYTES", 64)
    monkeypatch.setattr(scanner, "MIN_OVERLAP", 8)
    loader.load_all()
    path = tmp_path / "big.log"
    filler = "".join(f"line {i:04d}\n" for i in range(40))
    # Offsets chosen so both values straddle a 64-byte chunk edge.
    text = filler[:50] + PROVIDER_KEY + "\n" + filler[:100] + "x" * 20 + VALUE_SSH + "\ntail\n"
    path.write_text(text)
    assert text.index(PROVIDER_KEY) < 64 < text.index(PROVIDER_KEY) + len(PROVIDER_KEY)
    findings = scan_file(path, "big.log", loader)
    key_line = text[: text.index(PROVIDER_KEY)].count("\n") + 1
    ssh_line = text[: text.index(VALUE_SSH)].count("\n") + 1
    assert [(f.check, f.location) for f in findings] == [
        ("credential-pattern", f"big.log:{key_line}"),
        ("held-secret", f"big.log:{ssh_line}"),
    ]


def test_scanner_command_fails_when_a_credential_file_mode_is_loosened(
    tmp_path: Path, secrets_dir: Path
) -> None:
    root = artifact_tree(tmp_path / "tree")
    (secrets_dir / ".ssh.creds").chmod(0o644)
    result = scan_command(root, secrets_dir)
    assert result.returncode == 1
    assert ".ssh.creds: mode is 644, not 600" in result.stdout
    assert "synthetic" not in output(result)


def test_scanner_command_reports_a_credential_file_it_cannot_parse(
    tmp_path: Path, secrets_dir: Path
) -> None:
    """Never silent: the values that were not compared are named on every run."""
    root = artifact_tree(tmp_path / "tree")
    write_secret_file(secrets_dir / ".ssh.creds", f"host {VALUE_SSH}\n")
    result = scan_command(root, secrets_dir)
    assert result.returncode == 0, output(result)
    assert "ARTIFACT WARN secrets-loader:" in result.stdout
    assert "NOT COMPARED: .ssh.creds" in result.stdout
    strict = scan_command(root, secrets_dir, "--strict-secrets")
    assert strict.returncode == 1
    assert "synthetic" not in output(result) + output(strict)


def test_make_lint_runs_the_artifact_scanner() -> None:
    planned = run("make", "-n", "lint")
    assert planned.returncode == 0, output(planned)
    assert "python -m trina.security" in planned.stdout
    assert "scripts/check_hygiene.py" in planned.stdout


def test_make_artifacts_fails_on_a_planted_credential(tmp_path: Path, secrets_dir: Path) -> None:
    """The real Makefile target, pointed at a scratch tree through its working directory."""
    root = artifact_tree(tmp_path / "tree")
    env = subprocess_env(secrets_dir, UV_PROJECT=str(REPO))
    make = ("make", "-f", REPO / "Makefile", "-C", root, "artifacts")
    clean = run(*make, env=env)
    assert clean.returncode == 0, output(clean)
    assert "artifacts: OK (3 file(s) scanned" in clean.stdout
    (root / "data" / "ds" / "leak.txt").write_text(f"x\ny\n{CHAT_KEY}\n")
    failed = run(*make, env=env)
    assert failed.returncode != 0
    assert "ARTIFACT FAIL credential-pattern data/ds/leak.txt:3: api_key [" in failed.stdout
    assert CHAT_KEY not in output(failed)


def test_hygiene_gate_uses_the_shared_catalogue(tmp_path: Path) -> None:
    """A shape only the package catalogue knows fails the tracked-file gate."""
    root = tmp_path / "repo"
    root.mkdir()
    git = ("git", "-c", "user.name=t", "-c", "user.email=t@example.test")
    assert run(*git, "init", "-q", cwd=root).returncode == 0
    (root / ".gitignore").write_text(IGNORES)
    (root / ".hyperfideignore").write_text(IGNORES)
    (root / "notes.txt").write_text("nothing here\n")
    assert run(*git, "add", "-A", cwd=root).returncode == 0
    empty = tmp_path / "no-secrets"
    empty.mkdir()
    hygiene = (PYTHON, HYGIENE, "--root", root, "--secrets-dir", empty)
    clean = run(*hygiene)
    assert clean.returncode == 0, output(clean)
    (root / "notes.txt").write_text(f"nothing here\n{CHAT_KEY}\n")
    failed = run(*hygiene)
    assert failed.returncode == 1
    assert "HYGIENE FAIL credential-pattern notes.txt:2: api_key [" in failed.stdout
    assert CHAT_KEY not in output(failed)


def test_hygiene_gate_passes_on_this_repository_with_the_fixture_scanned() -> None:
    """The fixture is scanned like any tracked file: its shapes exist only once joined."""
    result = run(PYTHON, HYGIENE, env=subprocess_env(REPO / "tests"))
    assert result.returncode == 0, output(result)
    raw = FIXTURE.read_text(encoding="utf-8")
    catalogue = [regex for _, regex in credential_patterns()]
    assert not any(regex.search(raw) for regex in catalogue)
    assert all(regex.search(raw.replace(JOIN, "")) for regex in catalogue)
