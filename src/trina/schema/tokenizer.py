"""The declared budget tokenizer interface (clause 1.4, design section 4.4).

Every budget computation in training, evaluation and serving calls ``count_tokens``
and passes the tokenizer it was configured with; there is no implicit tokenizer.
Encoder tokenizers a model uses for its own input limit are not budget tokenizers.

The default budget tokenizer is the reader's tokenizer (decision P12). No local
implementation of it is available to this package, so the default implementation
here is the declared stand-in ``StandInTokenizer``; its ``id`` is recorded wherever
a count is recorded, so a count can never be mistaken for the reader's.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from trina.schema.errors import ContractError
from trina.schema.types import Span


class TokenizerError(ContractError):
    """A tokenizer broke its interface, for example by returning a negative count."""


@runtime_checkable
class Tokenizer(Protocol):
    """What a budget tokenizer provides."""

    @property
    def id(self) -> str:
        """Name and version, recorded in runs and bundles."""
        ...

    def count(self, text: str) -> int:
        """Number of tokens in ``text``."""
        ...

    def truncate(self, text: str, limit: int) -> tuple[str, Span]:
        """A contiguous part of ``text`` of at most ``limit`` tokens, and its span."""
        ...


class StandInTokenizer:
    """Deterministic, dependency-free stand-in for the reader's tokenizer.

    A run of word characters costs one token per ``PIECE`` code points, rounded up;
    every other non-space character costs one token; whitespace is free. The rule
    is fixed by ``id``: changing it means a new version.
    """

    PIECE = 4
    _RUN_RE = re.compile(r"\w+|[^\w\s]")

    @property
    def id(self) -> str:
        return "trina-standin-v1"

    def _pieces(self, text: str) -> Iterator[int]:
        """Yield the end offset of each token, in order."""
        for match in self._RUN_RE.finditer(text):
            start, end = match.span()
            yield from range(start + self.PIECE, end, self.PIECE)
            yield end

    def count(self, text: str) -> int:
        return sum(1 for _ in self._pieces(text))

    def truncate(self, text: str, limit: int) -> tuple[str, Span]:
        """Keep the longest head of ``text`` that costs at most ``limit`` tokens."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise TokenizerError("truncate limit must be a non-negative integer")
        end = 0
        for index, piece_end in enumerate(self._pieces(text)):
            if index == limit:
                return text[:end], Span(0, end)
            end = piece_end
        return text, Span(0, len(text))


def default_tokenizer() -> Tokenizer:
    """The one default implementation; callers still pass it explicitly."""
    return StandInTokenizer()


def count_tokens(text: str, tokenizer: Tokenizer) -> int:
    """The single function every budget computation calls."""
    if not isinstance(text, str):
        raise TokenizerError("count_tokens expects text")
    count = tokenizer.count(text)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise TokenizerError(f"tokenizer {tokenizer.id} returned an invalid count")
    return count
