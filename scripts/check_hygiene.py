#!/usr/bin/env python3
"""Credential hygiene gate (clauses 3.1 and 3.5). Runs inside ``make lint``.

Fails when:
  * a credential file exists with a mode other than 600;
  * a credential file is tracked or staged, or not ignored by git or by Hyperfide;
  * data/ or runs/ is not ignored by git or by Hyperfide;
  * a credential pattern occurs in a tracked or staged file.

Credential files are ignored and so are absent from task worktrees. They are located
in the checked tree and in the secrets directory: --secrets-dir, else
TRINA_SECRETS_DIR, else the main repository root resolved from git's common
directory. They are inspected with stat only; their contents are never read.

A matched value is never printed: a finding is file, line, pattern kind and a short
hash. Files holding synthetic secrets live under tests/fixtures/security/ and carry
the marker comment "trina-hygiene: synthetic-secrets" on a line of its own; the
marker anywhere else fails the gate.

The built-in catalogue is merged with ``trina.security.credential_patterns()`` when
the package provides it, so the gate and the redactor share one catalogue.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CREDENTIAL_FILES = (".env", ".ssh.creds")
OUTPUT_DIRS = ("data", "runs")
HYPERFIDE_IGNORE = ".hyperfideignore"
SECRETS_DIR_ENV = "TRINA_SECRETS_DIR"
EXEMPT_DIR = "tests/fixtures/security/"
EXEMPT_MARKER_RE = re.compile(
    r"^[ \t#/*<!-]*trina-hygiene: synthetic-secrets[ \t*/>-]*$", re.MULTILINE
)

_NAME = r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"


@dataclass(frozen=True)
class Pattern:
    kind: str
    regex: re.Pattern[str]
    group: int = 0
    # Assigned values must mix letters and digits, which keeps prose and names out.
    mixed: bool = False


@dataclass(frozen=True)
class Finding:
    check: str
    location: str
    detail: str


BUILTIN_PATTERNS = (
    Pattern("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    Pattern("api_key", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    Pattern("cloud_token", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Pattern("repo_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    Pattern("repo_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    Pattern("repo_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    Pattern("repo_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    Pattern("repo_token", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}")),
    Pattern("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
    Pattern("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    Pattern(
        "assignment",
        re.compile(
            r"(?i)\b[A-Za-z0-9_.-]*" + _NAME + r"[A-Za-z0-9_-]*[\"']?\s*[:=]\s*"
            r"[\"']([A-Za-z0-9+/_.=~-]{16,})[\"']"
        ),
        group=1,
        mixed=True,
    ),
    Pattern(
        "assignment",
        re.compile(
            r"^(?:export\s+)?[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|KEY)[A-Z0-9_]*="
            r"([^\s\"'#$<\[{(]{16,})\s*$",
            re.MULTILINE,
        ),
        group=1,
        mixed=True,
    ),
)


def package_patterns() -> list[Pattern]:
    """Patterns of the redaction catalogue, when the package provides one."""
    try:
        module = importlib.import_module("trina.security")
    except ModuleNotFoundError:
        return []
    provider = getattr(module, "credential_patterns", None)
    if provider is None:
        return []
    return [Pattern(kind, regex) for kind, regex in provider()]


def git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)


def git_paths(root: Path, *args: str) -> list[str] | None:
    result = git(root, *args)
    if result.returncode != 0:
        return None
    return sorted({os.fsdecode(part) for part in result.stdout.split(b"\0") if part})


def toplevel(directory: Path) -> Path | None:
    result = git(directory, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    return Path(os.fsdecode(result.stdout).strip())


def default_secrets_dir(root: Path) -> Path | None:
    configured = os.environ.get(SECRETS_DIR_ENV)
    if configured:
        return Path(configured)
    result = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if result.returncode != 0:
        return None
    common = Path(os.fsdecode(result.stdout).strip())
    return common.parent if common.name == ".git" else None


def git_ignored(root: Path, relative: str) -> bool:
    # Tracked paths are never reported as ignored by check-ignore.
    return git(root, "check-ignore", "-q", "--", relative).returncode == 0


def load_ignore_patterns(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def ignore_matches(patterns: list[str], relative: str, is_dir: bool) -> bool:
    """Gitignore-style match of a repository-relative path; the last match wins."""
    parts = relative.split("/")
    ignored = False
    for raw in patterns:
        negate = raw.startswith("!")
        pattern = raw[1:] if negate else raw
        dir_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        anchored = "/" in pattern
        pattern = pattern.lstrip("/")
        for index in range(1, len(parts) + 1):
            if dir_only and index == len(parts) and not is_dir:
                continue
            candidate = "/".join(parts[:index]) if anchored else parts[index - 1]
            if fnmatch.fnmatchcase(candidate, pattern):
                ignored = not negate
                break
    return ignored


def check_credential_files(root: Path, secrets_dir: Path | None) -> list[Finding]:
    findings: list[Finding] = []
    directories = [root]
    if secrets_dir is not None and secrets_dir.resolve() != root.resolve():
        directories.append(secrets_dir)
    for directory in directories:
        base = root if directory is root else toplevel(directory)
        for name in CREDENTIAL_FILES:
            path = directory / name
            present = path.exists()
            shown = name if directory is root else str(path)
            if present:
                mode = stat.S_IMODE(path.stat().st_mode)
                if mode != 0o600:
                    findings.append(Finding("file-mode", shown, f"mode is {mode:03o}, not 600"))
            # The checked tree must ignore the file even while it is absent; another
            # directory is checked only where the file exists inside a git work tree.
            if base is None or (directory is not root and not present):
                continue
            inside = directory.resolve().relative_to(base.resolve())
            relative = (inside / name).as_posix()
            if git_paths(base, "ls-files", "-z", "--cached", "--", relative):
                findings.append(Finding("git-ignore", shown, "is tracked or staged"))
            if not git_ignored(base, relative):
                findings.append(Finding("git-ignore", shown, "is not ignored by git"))
            hyperfide = load_ignore_patterns(base / HYPERFIDE_IGNORE)
            if not ignore_matches(hyperfide, relative, is_dir=False):
                findings.append(Finding("hyperfide-ignore", shown, "is not ignored by Hyperfide"))
    return findings


def check_output_dirs(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    hyperfide = load_ignore_patterns(root / HYPERFIDE_IGNORE)
    for name in OUTPUT_DIRS:
        if not git_ignored(root, f"{name}/hygiene-probe"):
            findings.append(Finding("git-ignore", f"{name}/", "is not ignored by git"))
        if not ignore_matches(hyperfide, name, is_dir=True):
            findings.append(Finding("hyperfide-ignore", f"{name}/", "is not ignored by Hyperfide"))
    return findings


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_blob(relative: str, shown: str, data: bytes, patterns: list[Pattern]) -> list[Finding]:
    text = data.decode("utf-8", errors="replace")
    marker = EXEMPT_MARKER_RE.search(text)
    if marker is not None:
        if relative.startswith(EXEMPT_DIR):
            return []
        location = f"{shown}:{line_of(text, marker.start())}"
        return [Finding("exemption", location, f"exemption marker outside {EXEMPT_DIR}")]
    found: set[tuple[int, str, str]] = set()
    for pattern in patterns:
        for match in pattern.regex.finditer(text):
            value = match.group(pattern.group)
            if pattern.mixed and not (
                any(char.isdigit() for char in value) and any(char.isalpha() for char in value)
            ):
                continue
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
            found.add((line_of(text, match.start(pattern.group)), pattern.kind, digest))
    return [
        Finding("credential-pattern", f"{shown}:{line}", f"{kind} [{digest}]")
        for line, kind, digest in sorted(found)
    ]


def scan_tracked(root: Path, patterns: list[Pattern]) -> tuple[list[Finding], int]:
    """Scan tracked and staged files: the working copy, and the index where it differs."""
    tracked = git_paths(root, "ls-files", "-z", "--cached")
    if tracked is None:
        return [Finding("tracked-scan", ".", "git ls-files failed")], 0
    changed = set(git_paths(root, "diff", "--name-only", "-z") or [])
    findings: list[Finding] = []
    for relative in tracked:
        path = root / relative
        if path.is_file() and not path.is_symlink():
            findings += scan_blob(relative, relative, path.read_bytes(), patterns)
        if relative in changed:
            staged = git(root, "show", f":{relative}")
            if staged.returncode == 0:
                findings += scan_blob(relative, f"{relative} (staged)", staged.stdout, patterns)
    return findings, len(tracked)


def run(root: Path, secrets_dir: Path | None) -> tuple[list[Finding], int, int]:
    if toplevel(root) is None:
        return [Finding("repository", str(root), "is not a git work tree")], 0, 0
    patterns = [*BUILTIN_PATTERNS, *package_patterns()]
    findings = check_credential_files(root, secrets_dir) + check_output_dirs(root)
    scanned, count = scan_tracked(root, patterns)
    return findings + scanned, count, len(patterns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Credential hygiene gate.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="work tree to check (default: the tree holding this script)",
    )
    parser.add_argument(
        "--secrets-dir",
        type=Path,
        default=None,
        help=f"directory holding the credential files (default: ${SECRETS_DIR_ENV}, "
        "else the main repository root)",
    )
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    secrets_dir: Path | None = args.secrets_dir or default_secrets_dir(root)
    findings, files, patterns = run(root, secrets_dir)
    for finding in findings:
        print(f"HYGIENE FAIL {finding.check} {finding.location}: {finding.detail}")
    if findings:
        print(f"hygiene: FAIL ({len(findings)} finding(s))")
        return 1
    print(f"hygiene: OK ({files} tracked files scanned, {patterns} patterns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
