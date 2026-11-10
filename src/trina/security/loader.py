"""The secrets loader: the only reader of credentials (security.md section 2).

Credentials come from two sources only, the process environment and the ignored
credential files ``.env`` and ``.ssh.creds``. The files are ignored and so are absent
from task worktrees; they are looked for in the secrets directory: the constructor
argument, else ``TRINA_SECRETS_DIR``, else the main repository root resolved from
git's common directory. A file is read only when its mode is 600.

A value is returned as a :class:`Secret`, which shows a fixed placeholder wherever it
is printed, formatted or logged and refuses to be pickled. The raw value leaves it
through :meth:`Secret.reveal` only, at the point of use. The loader never writes a
credential, and its errors carry names, paths and line numbers, never content.

As a second line of defence the loader can replace the values it holds in any text
(:meth:`SecretsLoader.scrub`), which the log and excepthook guards below apply to
every log record and uncaught traceback.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import stat
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import quote

CREDENTIAL_FILES = (".env", ".ssh.creds")
SECRETS_DIR_ENV = "TRINA_SECRETS_DIR"
SECRET_PLACEHOLDER = "[SECRET]"
# A held value shorter than this is not compared against text: a short user name or
# port would match everywhere. Such values are reported by uncomparable_names().
MIN_COMPARABLE = 8

_LINE_RE = re.compile(r"^(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*)$")
_DOUBLE_QUOTED_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"[ \t]*(?:#.*)?$')
_SINGLE_QUOTED_RE = re.compile(r"^'([^']*)'[ \t]*(?:#.*)?$")
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t"}


class SecretsError(Exception):
    """Base of the loader's errors. No message holds a credential value."""


class MissingSecretError(SecretsError):
    """A credential name is in neither the environment nor a credential file."""

    def __init__(self, name: str, secrets_dir: Path | None) -> None:
        self.name = name
        where = (
            f"the credential files in {secrets_dir}"
            if secrets_dir is not None
            else f"no secrets directory was found (set {SECRETS_DIR_ENV})"
        )
        super().__init__(f"{name}: not in the environment; {where}")


class CredentialFileError(SecretsError):
    """A credential file cannot be used: wrong mode, wrong type or a malformed line."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class CredentialFormatError(CredentialFileError):
    """A credential file has a line that is not ``NAME=VALUE``.

    The file's values cannot be read by name. The error is kept, not raised, while
    the name asked for is found elsewhere; see :meth:`SecretsLoader.unparsed`.
    """


class Secret:
    """An opaque credential value.

    ``repr``, ``str`` and every format yield the fixed placeholder; pickling is
    refused. Only :meth:`reveal` returns the raw value.
    """

    __slots__ = ("__value", "name")

    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.__value = value

    def reveal(self) -> str:
        """The raw value, for the point of use only. Do not store or log the result."""
        return self.__value

    def __repr__(self) -> str:
        return SECRET_PLACEHOLDER

    def __str__(self) -> str:
        return SECRET_PLACEHOLDER

    def __format__(self, format_spec: str) -> str:
        return SECRET_PLACEHOLDER

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self.__value.encode(), other.__value.encode())

    def __hash__(self) -> int:
        return id(self)

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("a Secret is not serializable; pass the credential name instead")

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        return self


def _unquote(raw: str) -> str | None:
    """The value of the right-hand side of a credential-file line, or None."""
    double = _DOUBLE_QUOTED_RE.match(raw)
    if double is not None:
        return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), double.group(1))
    single = _SINGLE_QUOTED_RE.match(raw)
    if single is not None:
        return single.group(1)
    if raw[:1] in ("'", '"'):
        return None
    # Unquoted: a comment starts at whitespace followed by '#'.
    return re.split(r"[ \t]+#", raw, maxsplit=1)[0].rstrip()


def _read_credential_file(path: Path) -> dict[str, str]:
    """Parse ``NAME=VALUE`` lines of a mode-600 regular file."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise CredentialFileError(path, f"cannot be opened ({type(exc).__name__})") from None
    with os.fdopen(descriptor, "rb") as handle:
        status = os.fstat(handle.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise CredentialFileError(path, "is not a regular file")
        mode = stat.S_IMODE(status.st_mode)
        if mode != 0o600:
            raise CredentialFileError(path, f"mode is {mode:03o}, not 600")
        data = handle.read()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CredentialFileError(path, "is not UTF-8") from None
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE_RE.match(stripped)
        value = _unquote(match.group(2)) if match is not None else None
        if match is None or value is None:
            # The line itself is never quoted: it may hold a credential.
            raise CredentialFormatError(path, f"line {number} is not NAME=VALUE")
        values[match.group(1)] = value
    return values


def _git_main_root(start: Path) -> Path | None:
    """The main repository root, resolved from git's common directory."""
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    common = Path(os.fsdecode(result.stdout).strip())
    return common.parent if common.name == ".git" else None


def resolve_secrets_dir(
    environ: Mapping[str, str] | None = None, start: Path | None = None
) -> Path | None:
    """``TRINA_SECRETS_DIR``, else the main repository root, else None."""
    env = os.environ if environ is None else environ
    configured = env.get(SECRETS_DIR_ENV)
    if configured:
        path = Path(configured)
        if not path.is_dir():
            raise CredentialFileError(path, f"{SECRETS_DIR_ENV} is not a directory")
        return path
    for candidate in (start or Path.cwd(), Path(__file__).resolve().parent):
        root = _git_main_root(candidate)
        if root is not None:
            return root
    return None


class SecretsLoader:
    """Reads credentials from the environment, then ``.env``, then ``.ssh.creds``.

    The environment wins over the files, and ``.env`` over ``.ssh.creds``. Files are
    read once, on first need. Every value the loader has handed out or read from a
    credential file is *held*: :meth:`scrub` and :meth:`occurrences` compare text
    against the held values in memory.

    A file with a wrong mode is an error at once. A file with a malformed line holds
    nothing: the error is kept in :meth:`unparsed` and raised when a name is asked
    for that no other source has, since the name may be in that file.
    """

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        secrets_dir: Path | None = None,
    ) -> None:
        self._environ: Mapping[str, str] = os.environ if environ is None else environ
        self._explicit_dir = secrets_dir
        self._lock = threading.Lock()
        self._files: dict[str, str] | None = None
        self._dir: Path | None = None
        self._held: dict[str, str] = {}
        self._unparsed: list[CredentialFormatError] = []

    @property
    def secrets_dir(self) -> Path | None:
        self._load_files()
        return self._dir

    def _load_files(self) -> dict[str, str]:
        with self._lock:
            if self._files is None:
                directory = self._explicit_dir
                if directory is None:
                    directory = resolve_secrets_dir(self._environ)
                elif not directory.is_dir():
                    raise CredentialFileError(directory, "is not a directory")
                merged: dict[str, str] = {}
                if directory is not None:
                    # Reversed, so that the first file wins.
                    for name in reversed(CREDENTIAL_FILES):
                        path = directory / name
                        if not path.exists():
                            continue
                        try:
                            merged.update(_read_credential_file(path))
                        except CredentialFormatError as exc:
                            self._unparsed.insert(0, exc)
                self._dir = directory
                self._files = merged
                self._held.update({name: value for name, value in merged.items() if value})
            return self._files

    def get_optional(self, name: str) -> Secret | None:
        """The credential called ``name``, or None when no source has it."""
        value = self._environ.get(name)
        if not value:
            value = self._load_files().get(name)
        if not value:
            return None
        with self._lock:
            self._held[name] = value
        return Secret(name, value)

    def get(self, name: str) -> Secret:
        """The credential called ``name``; MissingSecretError when no source has it."""
        secret = self.get_optional(name)
        if secret is None:
            if self._unparsed:
                raise self._unparsed[0]
            raise MissingSecretError(name, self._dir)
        return secret

    def unparsed(self) -> list[CredentialFormatError]:
        """Format errors of credential files whose values could not be read."""
        self._load_files()
        return list(self._unparsed)

    def names(self) -> list[str]:
        """Names defined in the credential files. Names only, never values."""
        return sorted(self._load_files())

    def load_all(self) -> int:
        """Read the credential files so their values are held; returns how many are."""
        self._load_files()
        return len(self._held)

    def uncomparable_names(self) -> list[str]:
        """Held names whose value is too short to compare against text."""
        with self._lock:
            return sorted(n for n, v in self._held.items() if len(v) < MIN_COMPARABLE)

    def _needles(self) -> list[tuple[str, str]]:
        """(needle, name) for every comparable held value and its common encodings."""
        with self._lock:
            held = dict(self._held)
        needles: dict[str, str] = {}
        for name, value in held.items():
            if len(value) < MIN_COMPARABLE:
                continue
            for form in (value, quote(value, safe=""), json.dumps(value)[1:-1]):
                needles.setdefault(form, name)
        # Longest first, so a value that contains another is replaced whole.
        return sorted(needles.items(), key=lambda item: (-len(item[0]), item[0]))

    def scrub(self, text: str) -> str:
        """``text`` with every held value replaced by the fixed placeholder."""
        for needle, _ in self._needles():
            if needle in text:
                text = text.replace(needle, SECRET_PLACEHOLDER)
        return text

    def occurrences(self, text: str) -> Iterator[tuple[str, int]]:
        """(credential name, offset) for every held value found in ``text``."""
        for needle, name in self._needles():
            offset = text.find(needle)
            while offset != -1:
                yield name, offset
                offset = text.find(needle, offset + len(needle))

    def longest_needle(self) -> int:
        """Length of the longest comparable form: the overlap a chunked reader needs."""
        return max((len(needle) for needle, _ in self._needles()), default=0)


# -- guards: the second line of defence -------------------------------------------


def scrub_log_record(record: logging.LogRecord, loader: SecretsLoader) -> None:
    """Replace held values in a record's message, traceback and string fields."""
    try:
        message = record.getMessage()
    except Exception:  # a broken format string must not lose the record
        message = f"{record.msg!s} {record.args!r}"
    record.msg = loader.scrub(message)
    record.args = None
    if record.exc_info is not None and record.exc_info[0] is not None:
        formatted = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
        record.exc_text = loader.scrub(formatted)
        # Handlers that format exc_info themselves would bypass the scrubbed text.
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = loader.scrub(record.exc_text)
    for key, value in list(vars(record).items()):
        if key not in ("msg", "exc_text") and isinstance(value, str):
            setattr(record, key, loader.scrub(value))


class SecretLogFilter(logging.Filter):
    """A handler or logger filter that scrubs held values, ``extra`` fields included."""

    def __init__(self, loader: SecretsLoader) -> None:
        super().__init__()
        self._loader = loader

    def filter(self, record: logging.LogRecord) -> bool:
        scrub_log_record(record, self._loader)
        return True


def install_log_guard(loader: SecretsLoader) -> Callable[[], None]:
    """Scrub every log record of the process at creation; returns the uninstaller.

    The record factory is global, so the guard covers every logger and handler.
    ``extra`` fields are attached after creation: put a SecretLogFilter on a handler
    to cover those as well.
    """
    previous = logging.getLogRecordFactory()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        scrub_log_record(record, loader)
        return record

    logging.setLogRecordFactory(factory)

    def uninstall() -> None:
        if logging.getLogRecordFactory() is factory:
            logging.setLogRecordFactory(previous)

    return uninstall


def install_excepthook_guard(loader: SecretsLoader) -> Callable[[], None]:
    """Scrub the traceback of an uncaught exception; returns the uninstaller."""
    previous = sys.excepthook

    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(loader.scrub(text))
        sys.stderr.flush()

    sys.excepthook = hook

    def uninstall() -> None:
        if sys.excepthook is hook:
            sys.excepthook = previous

    return uninstall


def install_guards(loader: SecretsLoader) -> Callable[[], None]:
    """Install the log guard and the excepthook guard; returns one uninstaller."""
    uninstallers = (install_log_guard(loader), install_excepthook_guard(loader))

    def uninstall() -> None:
        for remove in reversed(uninstallers):
            remove()

    return uninstall
