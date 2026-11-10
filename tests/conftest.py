"""Shared pytest configuration: external-dependency markers and ``--require``.

A test that needs an external dependency carries one of the markers ``network``,
``bucket``, ``teacher`` or ``gpu``. When the dependency is unmet the test reports
SKIPPED with a reason. ``--require NAME[,NAME]`` names dependencies that must be met:
a test marked with a required dependency fails instead of skipping, a skip raised
inside such a test is turned into a failure, and the run is refused when no selected
test carries a required marker. A verify_cmd that names its dependency therefore
cannot pass on skips (clause 15.1).

Probes never read a credential. ``network`` opens one TCP connection. The other
dependencies are probed by the package that owns them: it exposes
``probe_dependency() -> str | None`` (None when met, otherwise a reason that holds no
secret) in the module named in PACKAGE_PROBES. Until that module exists the
dependency is unmet.
"""

from __future__ import annotations

import importlib
import os
import socket
from collections.abc import Callable, Generator

import pytest

DEPENDENCIES = ("network", "bucket", "teacher", "gpu")
DESCRIPTIONS = {
    "network": "needs outbound network access",
    "bucket": "needs the live source bucket",
    "teacher": "needs the live teacher provider",
    "gpu": "needs the GPU host",
}

# host:port the network probe connects to; the default is the package index.
NETWORK_PROBE_ENV = "TRINA_NETWORK_PROBE"
DEFAULT_NETWORK_PROBE = "pypi.org:443"

PACKAGE_PROBES = {
    "bucket": "trina.data.bucket",
    "teacher": "trina.teacher.client",
    "gpu": "trina.remote.bootstrap",
}
PROBE_FUNCTION = "probe_dependency"

_REQUIRED = pytest.StashKey[frozenset[str]]()
_UNMET = pytest.StashKey[dict[str, str | None]]()


def _probe_network() -> str | None:
    target = os.environ.get(NETWORK_PROBE_ENV, DEFAULT_NETWORK_PROBE)
    host, sep, port = target.rpartition(":")
    if not sep or not host or not port.isdigit():
        return f"{NETWORK_PROBE_ENV} must be host:port"
    try:
        with socket.create_connection((host, int(port)), timeout=5.0):
            return None
    except OSError as exc:
        return f"no TCP connection to {target} ({type(exc).__name__})"


def _probe_package(name: str) -> str | None:
    module_name = PACKAGE_PROBES[name]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        return f"no probe yet: module {exc.name} does not exist"
    probe: Callable[[], object] | None = getattr(module, PROBE_FUNCTION, None)
    if probe is None:
        return f"no probe yet: {module_name}.{PROBE_FUNCTION} is not defined"
    result = probe()
    return None if result is None else str(result)


def _unmet_reason(config: pytest.Config, name: str) -> str | None:
    """Reason the dependency is unmet, or None when it is met. Probed once per run."""
    cache = config.stash[_UNMET]
    if name not in cache:
        cache[name] = _probe_network() if name == "network" else _probe_package(name)
    return cache[name]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require",
        action="append",
        default=[],
        metavar="DEP[,DEP]",
        help="external dependencies that must be met (" + ", ".join(DEPENDENCIES) + "); "
        "an unmet or skipped required dependency is a failure",
    )


def pytest_configure(config: pytest.Config) -> None:
    for name in DEPENDENCIES:
        config.addinivalue_line("markers", f"{name}: {DESCRIPTIONS[name]}")
    values: list[str] = config.getoption("require")
    names = {part.strip() for value in values for part in value.split(",") if part.strip()}
    unknown = sorted(names - set(DEPENDENCIES))
    if unknown:
        raise pytest.UsageError(
            f"--require: unknown dependency {', '.join(unknown)} "
            f"(known: {', '.join(DEPENDENCIES)})"
        )
    config.stash[_REQUIRED] = frozenset(names)
    config.stash[_UNMET] = {}


def pytest_collection_finish(session: pytest.Session) -> None:
    required = session.config.stash[_REQUIRED]
    for name in sorted(required):
        if not any(item.get_closest_marker(name) for item in session.items):
            raise pytest.UsageError(
                f"--require {name}: no selected test is marked '{name}', "
                "so the dependency would not be exercised"
            )


def pytest_runtest_setup(item: pytest.Item) -> None:
    required = item.config.stash[_REQUIRED]
    marked = [name for name in DEPENDENCIES if item.get_closest_marker(name) is not None]
    # Required dependencies first, so a required one is never hidden behind a skip.
    for name in sorted(marked, key=lambda dep: dep not in required):
        reason = _unmet_reason(item.config, name)
        if reason is None:
            continue
        if name in required:
            pytest.fail(f"required dependency '{name}' is unmet: {reason}", pytrace=False)
        pytest.skip(f"dependency '{name}' is unmet: {reason}")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.skipped and not hasattr(report, "wasxfail"):
        required = item.config.stash[_REQUIRED]
        named = sorted(name for name in required if item.get_closest_marker(name) is not None)
        if named:
            longrepr = report.longrepr
            reason = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
            report.outcome = "failed"
            report.longrepr = (
                f"required dependency {', '.join(named)}: the test was skipped, "
                f"and a skip is not a pass ({reason})"
            )
    return report


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config
) -> None:
    stats = terminalreporter.stats
    passed, failed, errors, skipped = (
        len(stats.get(key, [])) for key in ("passed", "failed", "error", "skipped")
    )
    terminalreporter.write_line(
        f"trina gate: passed={passed} failed={failed} errors={errors} skipped={skipped} "
        "(a skipped test is not a pass)"
    )
    required = config.stash[_REQUIRED]
    for name, reason in sorted(config.stash[_UNMET].items()):
        if reason is not None:
            state = "REQUIRED but unmet" if name in required else "unmet"
            terminalreporter.write_line(f"trina gate: dependency '{name}' {state}: {reason}")
