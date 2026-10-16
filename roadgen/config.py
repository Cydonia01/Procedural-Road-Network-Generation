"""Default parameters, loaded from roadgen/defaults.yaml.

DEFAULTS holds the packaged defaults; the params dataclasses take their field
defaults from it. load_config() layers a user YAML file on top, which the CLI
uses for its ``--config`` option.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional, Union

import yaml

DEFAULTS_PATH = Path(__file__).with_name("defaults.yaml")


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge: nested mappings merge key by key, anything else replaces."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _check_keys(base: Any, override: Any, where: str) -> None:
    """Reject keys the defaults don't have (typos would silently do nothing)."""
    if not isinstance(base, dict) or not isinstance(override, dict) or where.endswith(".network"):
        return
    unknown = set(override) - set(base)
    if unknown:
        raise ValueError(f"unknown config keys under {where or 'top level'}: {sorted(unknown)}")
    for key in override:
        _check_keys(base[key], override[key], f"{where}.{key}" if where else key)


def _read(path: Union[str, Path]) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def load_config(path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """Packaged defaults, deep-merged with the YAML file at path (if given)."""
    config = _read(DEFAULTS_PATH)
    if path:
        override = _read(path)
        _check_keys(config, override, "")
        config = merge(config, override)
    return config


def tuplify(value: Any) -> Any:
    """YAML gives lists where dataclasses use (nested) tuples."""
    if isinstance(value, list):
        return tuple(tuplify(v) for v in value)
    return value


def section(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    """A nested mapping of config, with lists turned into tuples."""
    for key in keys:
        config = config[key]
    return {k: tuplify(v) for k, v in config.items()}


DEFAULTS = load_config()
