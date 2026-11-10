"""Typed errors of configuration resolution (clause 11.1).

Messages name keys and layers and never quote a configured value.
"""

from __future__ import annotations


class ConfigError(ValueError):
    """Base of every error raised while declaring, loading or resolving configuration."""


class SchemaError(ConfigError):
    """A schema declaration is invalid: a bad key, an unknown kind or a clash."""


class LayerError(ConfigError):
    """A layer cannot be read, or sets one key twice."""

    def __init__(self, layer: str, reason: str) -> None:
        self.layer = layer
        self.reason = reason
        super().__init__(f"layer {layer}: {reason}")


class UnknownKeyError(ConfigError):
    """A layer sets a key the schema does not declare."""

    def __init__(self, key: str, layer: str) -> None:
        self.key = key
        self.layer = layer
        super().__init__(f"layer {layer}: unknown key {key}")


class ConfigValueError(ConfigError):
    """A layer sets a declared key to a value of the wrong kind."""

    def __init__(self, key: str, layer: str, reason: str) -> None:
        self.key = key
        self.layer = layer
        self.reason = reason
        super().__init__(f"layer {layer}: {key}: {reason}")


class MissingKeyError(ConfigError):
    """A declared key is set by no layer; there are no defaults in code."""

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        super().__init__("no layer sets " + ", ".join(keys))
