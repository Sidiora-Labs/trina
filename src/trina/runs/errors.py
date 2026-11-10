"""Typed error of the run record (clauses 11.1 and 11.6)."""

from __future__ import annotations


class RunRecordError(ValueError):
    """A run record cannot be written, or a stored one is incomplete or inconsistent.

    Messages name files and fields and never quote a recorded value.
    """
