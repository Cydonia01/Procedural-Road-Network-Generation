"""Locating esmini binaries via ESMINI_HOME."""

from __future__ import annotations

import os
from pathlib import Path

ESMINI_ENV_VAR = "ESMINI_HOME"

# Fallback when ESMINI_HOME is unset: the esmini/ folder at the repo root.
_BUNDLED_ESMINI = Path(__file__).resolve().parents[2] / "esmini"


def esmini_binary(name: str) -> Path:
    """Path to $ESMINI_HOME/bin/<name>; raises FileNotFoundError if unavailable."""
    binary_dir = esmini_binary_dir()
    for candidate in (binary_dir / name, binary_dir / f"{name}.exe"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{name} not found in {binary_dir}")


def esmini_library(name: str) -> Path:
    """Path to an esmini shared library such as 'esminiRMLib' in the bin folder."""
    binary_dir = esmini_binary_dir()
    for filename in (f"lib{name}.dylib", f"lib{name}.so", f"{name}.dll"):
        candidate = binary_dir / filename
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{name} library not found in {binary_dir}")


def esmini_binary_dir() -> Path:
    """$ESMINI_HOME/bin, falling back to the repo's bundled esmini/bin."""
    home = os.environ.get(ESMINI_ENV_VAR)
    if not home:
        if not _BUNDLED_ESMINI.is_dir():
            raise FileNotFoundError(f"{ESMINI_ENV_VAR} is not set")
        home = str(_BUNDLED_ESMINI)
    return Path(home) / "bin"
