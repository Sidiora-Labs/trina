"""Requirement 11.1: layered configuration resolved to one frozen document."""

from trina.config.base import BASE_SCHEMA, SEED_NAMES
from trina.config.errors import (
    ConfigError,
    ConfigValueError,
    LayerError,
    MissingKeyError,
    SchemaError,
    UnknownKeyError,
)
from trina.config.resolve import (
    Layer,
    ResolvedConfig,
    load_layer,
    parse_overrides,
    resolve,
    resolve_paths,
)
from trina.config.schema import KINDS, Schema

__all__ = [
    "BASE_SCHEMA",
    "KINDS",
    "SEED_NAMES",
    "ConfigError",
    "ConfigValueError",
    "Layer",
    "LayerError",
    "MissingKeyError",
    "ResolvedConfig",
    "Schema",
    "SchemaError",
    "UnknownKeyError",
    "load_layer",
    "parse_overrides",
    "resolve",
    "resolve_paths",
]
