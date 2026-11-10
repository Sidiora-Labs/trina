"""Task 0.1: scaffold, gates, dependency markers, hygiene gate and evidence recording.

Every test drives the real tool as a subprocess: the Makefile gates on this tree,
pytest with the shared conftest in a scratch directory, the hygiene script on
fixture repositories and on this repository, and the specification validator on the
pack and on copies with planted defects. Synthetic secrets are assembled at run
time so that no credential pattern is ever written to a tracked file.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

import trina

REPO = Path(__file__).resolve().parents[2]
PACK = REPO / "spec" / "trina-memory"
VALIDATOR = PACK / "tools" / "validate_spec.py"
HYGIENE = REPO / "scripts" / "check_hygiene.py"
EVIDENCE = REPO / "scripts" / "record_evidence.py"
PYTHON = sys.executable

IGNORES = ".env\n.ssh.creds\ndata/\nruns/\n"
SECRET_BODY = "a1B2c3D4" * 6


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
        timeout=900,
    )


def output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Scaffold and toolchain
# ---------------------------------------------------------------------------


def test_package_imports_and_cli_runs() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", trina.__version__)
    result = run(PYTHON, "-m", "trina.cli", "--version")
    assert result.returncode == 0, output(result)
    assert result.stdout.strip() == f"trina {trina.__version__}"


def test_toolchain_is_python_312_and_pinned() -> None:
    assert sys.version_info[:2] == (3, 12)
    assert (REPO / ".python-version").read_text().strip() == "3.12"
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    locked = {package["name"]: package for package in lock["package"]}
    for name in ("trina", "pytest", "ruff", "mypy"):
        assert name in locked, f"{name} is not pinned in uv.lock"
    for name in ("pytest", "ruff", "mypy"):
        assert re.fullmatch(r"\d+(\.\d+)+", locked[name]["version"])


def test_output_directories_are_ignored_by_git_and_hyperfide() -> None:
    for name in (".gitignore", ".hyperfideignore"):
        lines = (REPO / name).read_text().splitlines()
        for entry in (".env", ".ssh.creds", "data/", "runs/"):
            assert entry in lines, f"{entry} missing from {name}"


# ---------------------------------------------------------------------------
# Fleet gates on the real tree
# ---------------------------------------------------------------------------


def test_lint_gate_passes_on_the_real_tree() -> None:
    result = run("make", "lint")
    assert result.returncode == 0, output(result)
    assert "hygiene: OK" in result.stdout


def test_test_gate_counts_skips_apart_from_passes() -> None:
    # A subset that cannot recurse into this test; the Makefile target is the real one.
    selection = "tests/cases/test_0_1.py -q -k test_package_imports_and_cli_runs"
    result = run("make", "test", f"PYTEST_ARGS={selection}")
    assert result.returncode == 0, output(result)
    assert "trina gate: passed=1 failed=0 errors=0 skipped=0" in result.stdout


# ---------------------------------------------------------------------------
# Dependency markers and --require
# ---------------------------------------------------------------------------

NETWORK_TESTS = """
import pytest


def test_local():
    assert True


@pytest.mark.network
def test_needs_network():
    assert True
"""

SELF_SKIPPING_TESTS = """
import pytest


@pytest.mark.network
def test_gives_up():
    pytest.skip("gave up at run time")
"""

UNMARKED_TESTS = """
def test_local():
    assert True
"""


@contextmanager
def listening_port() -> Iterator[str]:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        yield f"127.0.0.1:{server.getsockname()[1]}"


def closed_port() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{probe.getsockname()[1]}"


def inner_pytest(
    directory: Path, tests: str, probe: str, *args: str
) -> subprocess.CompletedProcess[str]:
    """Run pytest in a scratch directory with the repository's real conftest."""
    directory.mkdir()
    shutil.copy(REPO / "tests" / "conftest.py", directory / "conftest.py")
    (directory / "pytest.ini").write_text(
        "[pytest]\naddopts = -ra --strict-markers -p no:cacheprovider\n"
    )
    (directory / "test_inner.py").write_text(tests)
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    env["TRINA_NETWORK_PROBE"] = probe
    return run(PYTHON, "-m", "pytest", ".", *args, cwd=directory, env=env)


def test_unmet_dependency_reports_skipped_with_a_reason(tmp_path: Path) -> None:
    result = inner_pytest(tmp_path / "inner", NETWORK_TESTS, closed_port())
    assert result.returncode == 0, output(result)
    assert "trina gate: passed=1 failed=0 errors=0 skipped=1" in result.stdout
    assert "dependency 'network' is unmet: no TCP connection" in result.stdout
    assert "trina gate: dependency 'network' unmet" in result.stdout


def test_required_unmet_dependency_fails_instead_of_skipping(tmp_path: Path) -> None:
    result = inner_pytest(
        tmp_path / "inner", NETWORK_TESTS, closed_port(), "--require", "network"
    )
    assert result.returncode == 1, output(result)
    assert "required dependency 'network' is unmet" in result.stdout
    assert "trina gate: passed=1 failed=0 errors=1 skipped=0" in result.stdout
    assert "trina gate: dependency 'network' REQUIRED but unmet" in result.stdout


def test_required_met_dependency_runs_the_test(tmp_path: Path) -> None:
    with listening_port() as probe:
        result = inner_pytest(tmp_path / "inner", NETWORK_TESTS, probe, "--require", "network")
    assert result.returncode == 0, output(result)
    assert "trina gate: passed=2 failed=0 errors=0 skipped=0" in result.stdout


def test_skip_inside_a_required_test_is_a_failure(tmp_path: Path) -> None:
    with listening_port() as probe:
        skipped = inner_pytest(tmp_path / "plain", SELF_SKIPPING_TESTS, probe)
        required = inner_pytest(
            tmp_path / "required", SELF_SKIPPING_TESTS, probe, "--require", "network"
        )
    assert skipped.returncode == 0, output(skipped)
    assert "trina gate: passed=0 failed=0 errors=0 skipped=1" in skipped.stdout
    assert required.returncode == 1, output(required)
    assert "a skip is not a pass" in required.stdout
    assert "trina gate: passed=0 failed=1 errors=0 skipped=0" in required.stdout


def test_require_rejects_unknown_and_unexercised_dependencies(tmp_path: Path) -> None:
    unknown = inner_pytest(tmp_path / "unknown", NETWORK_TESTS, closed_port(), "--require", "db")
    assert unknown.returncode == pytest.ExitCode.USAGE_ERROR, output(unknown)
    assert "unknown dependency db" in output(unknown)
    with listening_port() as probe:
        unexercised = inner_pytest(
            tmp_path / "unexercised", UNMARKED_TESTS, probe, "--require", "network"
        )
    assert unexercised.returncode == pytest.ExitCode.USAGE_ERROR, output(unexercised)
    assert "no selected test is marked 'network'" in output(unexercised)


# ---------------------------------------------------------------------------
# Hygiene gate
# ---------------------------------------------------------------------------


def make_repo(root: Path) -> Path:
    """A clean fixture repository: ignored mode-600 credential files, one tracked file."""
    root.mkdir()
    assert run("git", "init", "-q", cwd=root).returncode == 0
    (root / ".gitignore").write_text(IGNORES)
    (root / ".hyperfideignore").write_text(IGNORES)
    for name in (".env", ".ssh.creds"):
        (root / name).write_text("placeholder\n")
        (root / name).chmod(0o600)
    (root / "notes.md").write_text("Nothing secret here.\n")
    return root


def hygiene(root: Path) -> subprocess.CompletedProcess[str]:
    assert run("git", "add", "-A", cwd=root).returncode == 0
    return run(PYTHON, HYGIENE, "--root", root, "--secrets-dir", root)


def synthetic_secrets() -> dict[str, str]:
    return {
        "api_key": "key in a note: sk-" + SECRET_BODY,
        "cloud_token": "AKIA" + "ABCDEFGHIJKLMNOP",
        "repo_token": "ghp_" + SECRET_BODY[:36],
        "bearer": "Authorization: Bearer " + SECRET_BODY,
        "private_key": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "assignment": 'db_password = "' + SECRET_BODY[:24] + '"',
    }


def test_hygiene_passes_on_a_clean_fixture(tmp_path: Path) -> None:
    result = hygiene(make_repo(tmp_path / "repo"))
    assert result.returncode == 0, output(result)
    assert "hygiene: OK" in result.stdout


@pytest.mark.parametrize("kind", sorted(synthetic_secrets()))
def test_hygiene_fails_on_a_planted_credential(tmp_path: Path, kind: str) -> None:
    root = make_repo(tmp_path / "repo")
    (root / "leak.txt").write_text("first line\n" + synthetic_secrets()[kind] + "\n")
    result = hygiene(root)
    assert result.returncode == 1, output(result)
    assert re.search(rf"credential-pattern leak\.txt:2: {kind} \[[0-9a-f]{{8}}\]", result.stdout)
    # The finding names file, line, kind and a short hash, never the value.
    assert SECRET_BODY[:16] not in output(result)
    assert "ABCDEFGHIJKLMNOP" not in output(result)


def test_hygiene_fails_on_a_loosened_mode(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    (root / ".ssh.creds").chmod(0o644)
    result = hygiene(root)
    assert result.returncode == 1, output(result)
    assert "file-mode .ssh.creds: mode is 644, not 600" in result.stdout


def test_hygiene_fails_when_a_credential_file_is_not_git_ignored(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    (root / ".gitignore").write_text(IGNORES.replace(".env\n", ""))
    result = hygiene(root)
    assert result.returncode == 1, output(result)
    assert "git-ignore .env: is not ignored by git" in result.stdout
    assert "git-ignore .env: is tracked or staged" in result.stdout


def test_hygiene_fails_when_a_credential_file_is_not_hyperfide_ignored(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    (root / ".hyperfideignore").write_text(IGNORES.replace(".ssh.creds\n", ""))
    result = hygiene(root)
    assert result.returncode == 1, output(result)
    assert "hyperfide-ignore .ssh.creds: is not ignored by Hyperfide" in result.stdout


def test_hygiene_fails_when_an_output_directory_is_not_ignored(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    (root / ".gitignore").write_text(IGNORES.replace("data/\n", ""))
    (root / ".hyperfideignore").write_text(IGNORES.replace("runs/\n", ""))
    result = hygiene(root)
    assert result.returncode == 1, output(result)
    assert "git-ignore data/: is not ignored by git" in result.stdout
    assert "hyperfide-ignore runs/: is not ignored by Hyperfide" in result.stdout


def test_hygiene_exempts_no_path(tmp_path: Path) -> None:
    """Synthetic secrets are stored in fragments; a whole one fails in a fixture too."""
    root = make_repo(tmp_path / "repo")
    fixtures = root / "tests" / "fixtures" / "security"
    fixtures.mkdir(parents=True)
    (fixtures / "sample.txt").write_text(synthetic_secrets()["api_key"] + "\n")
    result = hygiene(root)
    assert result.returncode == 1, output(result)
    assert "credential-pattern tests/fixtures/security/sample.txt:1: api_key" in result.stdout


def test_hygiene_passes_on_the_repository() -> None:
    result = run(PYTHON, HYGIENE)
    assert result.returncode == 0, output(result)
    assert "hygiene: OK" in result.stdout


# ---------------------------------------------------------------------------
# Specification validator
# ---------------------------------------------------------------------------


def validate(pack: Path) -> subprocess.CompletedProcess[str]:
    return run(PYTHON, VALIDATOR, "--pack", pack, "--no-receipt")


def planted_pack(tmp_path: Path, old: str, new: str) -> Path:
    """A copy of spec/ whose spec.kvx has one planted defect."""
    shutil.copytree(REPO / "spec", tmp_path / "spec", ignore=shutil.ignore_patterns("__pycache__"))
    pack = tmp_path / "spec" / PACK.name
    spec = pack / "spec.kvx"
    text = spec.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"fixture text not unique in spec.kvx: {old!r}"
    spec.write_text(text.replace(old, new), encoding="utf-8")
    return pack


def test_validator_passes_on_the_specification_with_current_views() -> None:
    result = validate(PACK)
    assert result.returncode == 0, output(result)
    assert "PASS specification-authoring trina-memory" in result.stdout


def test_validator_detects_a_stale_derived_view(tmp_path: Path) -> None:
    pack = planted_pack(
        tmp_path,
        'title = "Memory, query and assembled-context contract"',
        'title = "A retitled task the derived views have not seen"',
    )
    result = validate(pack)
    assert result.returncode == 1, output(result)
    assert "implementation-plan.md: derived view is stale" in result.stdout


def test_validator_detects_a_dangling_reference(tmp_path: Path) -> None:
    pack = planted_pack(
        tmp_path,
        'reqs = ["3.1", "3.5", "15.1", "15.2", "15.3"]',
        'reqs = ["3.1", "3.5", "15.1", "15.2", "99.9"]',
    )
    result = validate(pack)
    assert result.returncode == 1, output(result)
    assert "task 0.1: reqs references unknown clause 99.9" in result.stdout


def test_validator_detects_an_uncovered_clause(tmp_path: Path) -> None:
    pack = planted_pack(
        tmp_path,
        "\n[design]\n",
        'ac_6 = "A planted clause that no task and no case covers."\n\n[design]\n',
    )
    result = validate(pack)
    assert result.returncode == 1, output(result)
    assert "clause 15.6 is not covered by any task" in result.stdout
    assert "clause 15.6 is not covered by any case" in result.stdout


# ---------------------------------------------------------------------------
# Evidence recording
# ---------------------------------------------------------------------------


def load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_spec", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_record_evidence_appends_a_parseable_entry(tmp_path: Path) -> None:
    target = tmp_path / "qualification.kvx"
    shutil.copy(PACK / "qualification.kvx", target)
    observed = 'said "done" with a \\ and\na second line # not a comment'
    script = 'import sys; print("evidence ok"); sys.exit(3)'
    arguments = [
        "--id", "wave0-sample", "--observed", observed, "--case", "TRM-026",
        "--file", str(target), "--log-dir", str(tmp_path / "logs"), "--allow-dirty",
        "--", PYTHON, "-c", script,
    ]  # fmt: skip
    result = run(PYTHON, EVIDENCE, *arguments)
    assert result.returncode == 3, output(result)

    # The specification's own strict parser is the oracle for the written entry.
    document = load_validator().parse_kvx(target.read_text(encoding="utf-8"), target.name)
    assert document.errors == []
    assert "observation.workspace" in document.sections
    entry = document.sections["evidence.wave0-sample"]
    head = run("git", "rev-parse", "HEAD").stdout.strip()
    assert entry["revision"] == head and re.fullmatch(r"[0-9a-f]{40,64}", head)
    assert script in entry["command"]
    assert entry["exit_code"] == 3
    assert entry["observed"] == observed
    assert entry["cases"] == ["TRM-026"]
    assert isinstance(entry["dirty"], bool)
    log = Path(entry["log"])
    assert log == tmp_path / "logs" / "wave0-sample.log"
    assert log.read_text().strip() == "evidence ok"

    before = target.read_text(encoding="utf-8")
    again = run(PYTHON, EVIDENCE, *arguments)
    assert again.returncode == 2, output(again)
    assert "already exists" in again.stderr
    assert target.read_text(encoding="utf-8") == before


def test_record_evidence_refuses_a_dirty_tree(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    target = root / "qualification.kvx"
    target.write_text('[summary]\nfeature = "fixture"\n')
    identity = ["-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
    assert run("git", "add", "-A", cwd=root).returncode == 0
    assert run("git", *identity, "commit", "-q", "-m", "fixture", cwd=root).returncode == 0
    base = ["--root", str(root), "--file", str(target), "--observed", "ran"]
    command = ["--", PYTHON, "-c", "print('ok')"]

    (root / "untracked.txt").write_text("not part of the revision\n")
    refused = run(PYTHON, EVIDENCE, "--id", "dirty", *base, *command)
    assert refused.returncode == 2, output(refused)
    assert "work tree is dirty" in refused.stderr
    assert "evidence." not in target.read_text()

    (root / "untracked.txt").unlink()
    first = run(PYTHON, EVIDENCE, "--id", "clean-1", *base, *command)
    assert first.returncode == 0, output(first)
    # The entry just written must not make the tree count as dirty for the next one.
    second = run(PYTHON, EVIDENCE, "--id", "clean-2", *base, *command)
    assert second.returncode == 0, output(second)
    text = target.read_text()
    assert "[evidence.clean-1]" in text and "[evidence.clean-2]" in text
    assert text.count("dirty = false") == 2
    assert (root / "runs" / "evidence" / "clean-1.log").read_text().strip() == "ok"
