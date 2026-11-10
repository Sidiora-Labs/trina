"""Artifact scanner: the scan gate for produced artifacts (clause 3.1).

Runs inside ``make lint`` as ``python -m trina.security`` over the ignored output
directories ``data/`` and ``runs/``. It fails when a credential pattern of the
catalogue, or a value held by the secrets loader, occurs in any file there
(security.md section 3). Values are compared in memory; a finding is file, line,
kind and a short hash, or the credential's name, never the matched text.

Files are streamed in chunks, so a large shard or checkpoint is never held in
memory. gzip, bzip2 and xz files are decompressed; zstd files are decompressed when
a zstd module is installed. A file the scanner cannot read through (zstd without a
module, Parquet) is a finding: an unscanned file is never a pass.
"""

from __future__ import annotations

import argparse
import bz2
import codecs
import gzip
import hashlib
import importlib
import lzma
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast

from trina.security.loader import SecretsError, SecretsLoader
from trina.security.patterns import SCAN_PATTERNS, find_matches

ARTIFACT_DIRS = ("data", "runs")
CHUNK_BYTES = 4 * 1024 * 1024
# Characters carried between chunks, so a match that spans a boundary is still seen.
MIN_OVERLAP = 64 * 1024

_STDLIB_OPENERS: dict[str, Callable[[Path], IO[bytes]]] = {
    ".gz": lambda path: cast(IO[bytes], gzip.open(path, "rb")),
    ".bz2": lambda path: cast(IO[bytes], bz2.open(path, "rb")),
    ".xz": lambda path: cast(IO[bytes], lzma.open(path, "rb")),
}
# Formats whose bytes hide their text and for which no reader is wired in.
_OPAQUE_SUFFIXES = (".parquet",)


@dataclass(frozen=True)
class ArtifactFinding:
    check: str
    location: str
    detail: str


@dataclass(frozen=True)
class ScanResult:
    findings: tuple[ArtifactFinding, ...]
    files: int
    held_values: int

    @property
    def passed(self) -> bool:
        return not self.findings


def _open_zstd(path: Path) -> IO[bytes] | None:
    """A decompressing reader from whichever zstd module is installed, or None."""
    for module_name in ("compression.zstd", "zstandard"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        return cast(IO[bytes], module.open(path, "rb"))
    return None


def _open(path: Path) -> IO[bytes] | None:
    """A byte reader over the file's content, or None when it cannot be read through."""
    suffix = path.suffix.lower()
    if suffix in _OPAQUE_SUFFIXES:
        return None
    if suffix in (".zst", ".zstd"):
        return _open_zstd(path)
    opener = _STDLIB_OPENERS.get(suffix)
    return opener(path) if opener is not None else path.open("rb")


def scan_text(text: str, loader: SecretsLoader | None) -> Iterator[tuple[int, str, str]]:
    """(offset, check, detail) for every credential pattern or held value in ``text``."""
    for match in find_matches(text, SCAN_PATTERNS):
        value = text[match.start : match.end]
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        yield match.start, "credential-pattern", f"{match.kind} [{digest}]"
    if loader is not None:
        for name, offset in loader.occurrences(text):
            yield offset, "held-secret", f"value of {name}"


def scan_file(path: Path, shown: str, loader: SecretsLoader | None) -> list[ArtifactFinding]:
    """Scan one file in chunks; findings carry line numbers of the decoded content."""
    try:
        handle = _open(path)
    except OSError as exc:
        return [ArtifactFinding("unscanned", shown, f"cannot be opened ({type(exc).__name__})")]
    if handle is None:
        return [ArtifactFinding("unscanned", shown, "no reader for this format; not scanned")]
    overlap = max(MIN_OVERLAP, loader.longest_needle() if loader is not None else 0)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    found: set[tuple[int, str, str]] = set()
    carry = ""
    line_base = 1
    try:
        with handle:
            while True:
                chunk = handle.read(CHUNK_BYTES)
                text = carry + decoder.decode(chunk, final=not chunk)
                for offset, check, detail in scan_text(text, loader):
                    found.add((line_base + text.count("\n", 0, offset), check, detail))
                if not chunk:
                    break
                carry = text[-overlap:]
                line_base += text.count("\n", 0, len(text) - len(carry))
    except (OSError, EOFError, ValueError) as exc:
        return [ArtifactFinding("unscanned", shown, f"cannot be read ({type(exc).__name__})")]
    return [
        ArtifactFinding(check, f"{shown}:{line}", detail) for line, check, detail in sorted(found)
    ]


def _files(directory: Path) -> Iterator[Path]:
    for current, names, files in os.walk(directory, followlinks=False):
        names.sort()
        for name in sorted(files):
            path = Path(current) / name
            if path.is_file() and not path.is_symlink():
                yield path


def scan_artifacts(
    root: Path, directories: Sequence[str] = ARTIFACT_DIRS, loader: SecretsLoader | None = None
) -> ScanResult:
    """Scan every file under the artifact directories of ``root``.

    A directory that does not exist holds no artifact and is not a finding.
    """
    findings: list[ArtifactFinding] = []
    files = 0
    for name in directories:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in _files(directory):
            files += 1
            findings += scan_file(path, path.relative_to(root).as_posix(), loader)
    held = loader.load_all() if loader is not None else 0
    return ScanResult(tuple(findings), files, held)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trina.security",
        description="Artifact scanner: fails when a credential pattern or a loaded secret "
        "value occurs in produced artifacts.",
    )
    parser.add_argument(
        "directories",
        nargs="*",
        default=list(ARTIFACT_DIRS),
        help="artifact directories under the root (default: data runs)",
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="tree to scan (default: current directory)"
    )
    parser.add_argument(
        "--no-secrets",
        action="store_true",
        help="patterns only: do not load credentials for the value comparison",
    )
    parser.add_argument(
        "--strict-secrets",
        action="store_true",
        help="fail, instead of warning, when a credential file cannot be parsed",
    )
    args = parser.parse_args(argv)
    loader: SecretsLoader | None = None
    skipped: list[str] = []
    if not args.no_secrets:
        loader = SecretsLoader()
        try:
            loader.load_all()
            unparsed = loader.unparsed()
        except SecretsError as exc:
            # Loader errors hold names, paths and line numbers only.
            print(f"ARTIFACT FAIL secrets-loader: {exc}")
            print("artifacts: FAIL (the credential files could not be loaded)")
            return 1
        for error in unparsed:
            level = "FAIL" if args.strict_secrets else "WARN"
            print(f"ARTIFACT {level} secrets-loader: {error}; its values are NOT compared")
            skipped.append(error.path.name)
        if skipped and args.strict_secrets:
            print("artifacts: FAIL (a credential file could not be parsed)")
            return 1
    result = scan_artifacts(args.root.resolve(), args.directories, loader)
    for finding in result.findings:
        print(f"ARTIFACT FAIL {finding.check} {finding.location}: {finding.detail}")
    if not result.passed:
        print(f"artifacts: FAIL ({len(result.findings)} finding(s) in {result.files} file(s))")
        return 1
    if loader is None:
        compared = "value comparison off"
    else:
        compared = f"{result.held_values} held value(s) compared"
        short = len(loader.uncomparable_names())
        if short:
            compared += f", {short} too short to compare"
        if skipped:
            compared += f", NOT COMPARED: {' '.join(skipped)}"
    print(
        f"artifacts: OK ({result.files} file(s) scanned, {len(SCAN_PATTERNS)} patterns, {compared})"
    )
    return 0
