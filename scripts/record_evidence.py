#!/usr/bin/env python3
"""Run a command and append its evidence entry to qualification.kvx (clause 15.2).

The main supervisor runs this when a wave lands; task workers do not. One entry is
one execution: the command is run here, its combined output is written to a log
file, and the entry records revision, command, exit_code, log and observed.

    scripts/record_evidence.py --id wave0-test --observed "what was seen" -- make test

The run is refused when the work tree differs from the recorded revision, unless
--allow-dirty is given, in which case the entry says so. The exit status is the
command's own; 2 means the entry was refused and nothing was recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_FILE = "spec/trina-memory/qualification.kvx"
DEFAULT_LOG_DIR = "runs/evidence"
ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
REFUSED = 2


def kvx_string(value: str) -> str:
    escapes = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r"}
    out = []
    for char in value:
        if char in escapes:
            out.append(escapes[char])
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def kvx_list(values: list[str]) -> str:
    return "[" + ", ".join(kvx_string(value) for value in values) + "]"


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def shown_path(path: Path, root: Path) -> str:
    """Path relative to the repository where it lies inside it."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def refuse(message: str) -> int:
    print(f"record_evidence: refused: {message}", file=sys.stderr)
    return REFUSED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a command and append its evidence entry to qualification.kvx."
    )
    parser.add_argument("--id", required=True, help="entry id; the section is [evidence.<id>]")
    parser.add_argument("--observed", required=True, help="what the execution showed")
    parser.add_argument("--case", action="append", default=[], help="case id (repeatable)")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--file", type=Path, default=None, help=f"default: {DEFAULT_FILE}")
    parser.add_argument("--log-dir", type=Path, default=None, help=f"default: {DEFAULT_LOG_DIR}")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- command to execute")
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    target: Path = (args.file or root / DEFAULT_FILE).resolve()
    log_dir: Path = (args.log_dir or root / DEFAULT_LOG_DIR).resolve()
    command: list[str] = args.command[1:] if args.command[:1] == ["--"] else args.command
    entry_id: str = args.id
    observed: str = args.observed

    if not ID_RE.fullmatch(entry_id):
        return refuse(f"invalid id {entry_id!r}")
    if not command:
        return refuse("no command given after --")
    if not observed.strip():
        return refuse("--observed must not be empty")
    if not target.is_file():
        return refuse(f"{target} does not exist")
    existing = target.read_text(encoding="utf-8")
    if re.search(rf"(?m)^\[evidence\.{re.escape(entry_id)}\]", existing):
        return refuse(f"[evidence.{entry_id}] already exists in {target.name}")

    revision = git(root, "rev-parse", "HEAD")
    if revision.returncode != 0:
        return refuse("no git revision: HEAD cannot be resolved")
    # The evidence file itself changes with every entry, so it does not count as dirt.
    pathspec = ["--", "."]
    if target.is_relative_to(root):
        pathspec.append(f":(exclude){target.relative_to(root).as_posix()}")
    status = git(root, "status", "--porcelain", *pathspec)
    if status.returncode != 0:
        return refuse("git status failed")
    dirty = bool(status.stdout.strip())
    if dirty and not args.allow_dirty:
        return refuse("the work tree is dirty, so the run would not match the revision")

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{entry_id}.log"
    if log_path.exists():
        return refuse(f"log {log_path} already exists")
    with log_path.open("wb") as log:
        try:
            exit_code = subprocess.run(
                command, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=False
            ).returncode
        except OSError as exc:
            log_path.unlink()
            return refuse(f"the command could not be started ({type(exc).__name__})")

    lines = [
        f"[evidence.{entry_id}]",
        f"recorded = {kvx_string(datetime.now(UTC).isoformat(timespec='seconds'))}",
        f"revision = {kvx_string(revision.stdout.strip())}",
        f"dirty = {'true' if dirty else 'false'}",
        f"command = {kvx_string(shlex.join(command))}",
        f"exit_code = {exit_code}",
        f"log = {kvx_string(shown_path(log_path, root))}",
        f"log_sha256 = {kvx_string(hashlib.sha256(log_path.read_bytes()).hexdigest())}",
    ]
    if args.case:
        lines.append(f"cases = {kvx_list(args.case)}")
    lines.append(f"observed = {kvx_string(observed)}")
    separator = "" if existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(separator + "\n".join(lines) + "\n")
    print(f"recorded [evidence.{entry_id}] exit_code={exit_code} log={shown_path(log_path, root)}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
