"""Code revision and hardware facts of a run record (clause 11.1).

Both are read from the local host: ``git`` for the revision, the operating system
and the installed distributions for the hardware facts. Nothing here opens a network
connection, and the host name, address and user are never recorded.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from trina.runs.errors import RunRecordError

DEVICE_KEYS = ("device_type", "gpu_model", "gpu_memory_bytes", "driver")
HARDWARE_KEYS = (
    "cpu_count",
    "ram_bytes",
    "platform",
    "machine",
    "python",
    "libraries",
    "lockfile_digest",
    *DEVICE_KEYS,
)

# Device facts of a run that uses no GPU. A run on a GPU passes the facts it measured.
CPU_DEVICE: Mapping[str, object] = MappingProxyType(
    {"device_type": "cpu", "gpu_model": None, "gpu_memory_bytes": None, "driver": None}
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CodeRevision:
    """Commit identity and, for a tree with uncommitted changes, a digest of them."""

    commit: str
    dirty: bool
    dirty_digest: str | None

    def __post_init__(self) -> None:
        if self.dirty != (self.dirty_digest is not None):
            raise RunRecordError("code_revision: a dirty tree, and only one, has a dirty digest")

    def to_json(self) -> dict[str, object]:
        return {"commit": self.commit, "dirty": self.dirty, "dirty_digest": self.dirty_digest}

    @classmethod
    def from_json(cls, value: object) -> CodeRevision:
        if not isinstance(value, Mapping) or set(value) != {"commit", "dirty", "dirty_digest"}:
            raise RunRecordError("code_revision: expected commit, dirty and dirty_digest")
        commit, dirty, dirty_digest = value["commit"], value["dirty"], value["dirty_digest"]
        if (
            not isinstance(commit, str)
            or not isinstance(dirty, bool)
            or not isinstance(dirty_digest, str | None)
        ):
            raise RunRecordError("code_revision: a field has the wrong type")
        return cls(commit, dirty, dirty_digest)


def _git(repo: Path, *args: str) -> bytes:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunRecordError(
            f"code revision: git {args[0]} failed ({type(exc).__name__}); on a host "
            "without git, pass the revision carried by the sync manifest"
        ) from None
    return done.stdout


def detect_code_revision(repo: Path) -> CodeRevision:
    """Read the revision of the working tree at ``repo`` from git.

    The dirty-tree digest covers the status listing, the diff of tracked files against
    the commit and the content of every untracked file that is not ignored, so two
    trees with the same digest hold the same code.
    """
    commit = _git(repo, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    status = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if not status:
        return CodeRevision(commit, False, None)
    top = Path(os.fsdecode(_git(repo, "rev-parse", "--show-toplevel")).rstrip("\n"))
    digest = hashlib.sha256(status)
    digest.update(_git(repo, "diff", "--binary", "--no-ext-diff", "HEAD"))
    untracked = _git(top, "ls-files", "--others", "--exclude-standard", "-z")
    for name in sorted(part for part in untracked.split(b"\0") if part):
        path = top / os.fsdecode(name)
        content = sha256_file(path) if path.is_file() and not path.is_symlink() else "-"
        digest.update(name + b"\0" + content.encode("ascii") + b"\0")
    return CodeRevision(commit, True, digest.hexdigest())


def detect_hardware(lockfile: Path, device: Mapping[str, object]) -> Mapping[str, object]:
    """Collect the hardware facts of this host.

    ``device`` holds the device facts (``DEVICE_KEYS``): ``CPU_DEVICE``, or what the
    training core measured on its GPU. ``lockfile`` is the dependency lockfile whose
    digest pins the installed libraries.
    """
    if set(device) != set(DEVICE_KEYS):
        raise RunRecordError("hardware: device facts must hold " + ", ".join(DEVICE_KEYS))
    cpu_count = os.cpu_count()
    if cpu_count is None:
        raise RunRecordError("hardware: the CPU count cannot be determined")
    try:
        ram_bytes = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        lockfile_digest = sha256_file(lockfile)
    except (OSError, ValueError) as exc:
        raise RunRecordError(
            f"hardware: RAM or lockfile cannot be read ({type(exc).__name__})"
        ) from None
    libraries = {
        str(dist.metadata["Name"]): dist.version for dist in importlib.metadata.distributions()
    }
    return MappingProxyType(
        {
            "cpu_count": cpu_count,
            "ram_bytes": ram_bytes,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "libraries": MappingProxyType(dict(sorted(libraries.items()))),
            "lockfile_digest": lockfile_digest,
            **device,
        }
    )
