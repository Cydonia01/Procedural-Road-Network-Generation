"""Run history: every successful CLI command, under a hash of its inputs.

A run's hash covers everything that decides its result: the fully resolved
command-line parameters (defaults included, so spelling a default out or
leaving it away gives the same hash) and the effective configuration
(packaged defaults merged with ``--config``). Equal inputs therefore give one
entry, never two.

The history is one JSON file (default ``runs/history.json``)::

    {"schema_version": 1,
     "runs":    {"<hash>": {"argv": [...], "config": "<config hash>",
                            "config_file": "...", "note": "...", "recorded": "...",
                            "geometry_version": 2}},
     "configs": {"<config hash>": {...effective configuration...}}}

Configurations are stored once each and shared by the runs that used them,
so replaying a hash (``python -m cli <hash>``) uses exactly the recorded
inputs, even after defaults.yaml or the --config file changed. The code
itself is not recorded: results can still change with it. Each run records
the road geometry version it was made with (roadgen.geometry.GEOMETRY_VERSION;
runs without one are version 1), so a replay can tell when it changed.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

SCHEMA_VERSION = 1
HASH_LENGTH = 12  # hex digits of sha256 kept
MIN_PREFIX = 4  # shortest hash prefix accepted by lookup
ENV_VAR = "ROADGEN_HISTORY"  # history file path; "" or "off" disables recording
DISABLED = ("", "off", "none", "0")
# Namespace keys that don't change a run's result.
IGNORED_PARAMS = frozenset({"cfg", "func", "config_file", "verbose", "note", "no_history"})
# Config sections that don't change a run's result.
IGNORED_CONFIG = frozenset({"history"})

PathLike = Union[str, Path]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=list)


def digest(value: Any, length: int = HASH_LENGTH) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()[:length]


def run_params(namespace: dict[str, Any]) -> dict[str, Any]:
    """The parsed arguments that decide a run's result, JSON-ready."""
    out = {}
    for key, value in namespace.items():
        if key in IGNORED_PARAMS:
            continue
        out[key] = list(value) if isinstance(value, tuple) else value
    return json.loads(_canonical(out))  # tuples -> lists, as they are stored


def config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in config.items() if k not in IGNORED_CONFIG}


def run_hash(params: dict[str, Any], config_hash: str) -> str:
    return digest({"params": params, "config": config_hash})


@dataclass(frozen=True)
class Run:
    hash: str
    argv: list[str]
    config: dict[str, Any]  # effective configuration
    config_file: Optional[str]
    note: str
    recorded: str
    geometry_version: int = 1

    def command_line(self) -> str:
        """The command as typed (without --config, whose content is in config)."""
        return " ".join(["python -m cli", *(_quote(a) for a in self.argv)])


def _quote(arg: str) -> str:
    return arg if arg and all(c.isalnum() or c in "-_./=:,[]+" for c in arg) else repr(arg)


class History:
    """The history file. Missing until the first run is recorded."""

    def __init__(self, path: PathLike) -> None:
        self.path = Path(path)

    @classmethod
    def from_env(cls, default: Optional[str]) -> Optional[History]:
        """The history at $ROADGEN_HISTORY or default; None if recording is off."""
        path = os.environ.get(ENV_VAR, default)
        if path is None or path.strip().lower() in DISABLED:
            return None
        return cls(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": SCHEMA_VERSION, "runs": {}, "configs": {}}
        data = json.loads(self.path.read_text())
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{self.path}: unsupported schema_version {data.get('schema_version')!r}")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        """Atomic write: a crash never leaves a half-written history."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=1, sort_keys=False)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def record(self, argv: list[str], params: dict[str, Any], config: dict[str, Any],
               config_file: Optional[str] = None, note: Optional[str] = None,
               geometry_version: Optional[int] = None) -> tuple[str, bool]:
        """(hash, whether it is new). An existing run keeps its argv; a note replaces its note."""
        data = self._load()
        snapshot = config_snapshot(config)
        config_hash = digest(snapshot)
        h = run_hash(params, config_hash)
        known = data["runs"].get(h)
        if known is not None:
            if note and note != known.get("note"):
                known["note"] = note
                self._save(data)
            return h, False
        stored = data["configs"].get(config_hash)
        if stored is not None and digest(stored) != config_hash:
            raise ValueError(f"{self.path}: config hash collision on {config_hash}")
        data["configs"].setdefault(config_hash, snapshot)
        data["runs"][h] = {
            "argv": list(argv),
            "config": config_hash,
            "config_file": config_file,
            "note": note or "",
            "recorded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        if geometry_version is not None:
            data["runs"][h]["geometry_version"] = geometry_version
        self._save(data)
        return h, True

    def runs(self) -> list[Run]:
        """All runs, oldest first."""
        data = self._load()
        return [self._run(data, h) for h in data["runs"]]

    def get(self, prefix: str) -> Run:
        """The run whose hash starts with prefix (at least MIN_PREFIX digits, unique)."""
        prefix = prefix.lower()
        if len(prefix) < MIN_PREFIX:
            raise KeyError(f"hash prefix {prefix!r} is too short (need {MIN_PREFIX}+ digits)")
        data = self._load()
        matches = [h for h in data["runs"] if h.startswith(prefix)]
        if not matches:
            raise KeyError(f"no run {prefix!r} in {self.path}")
        if len(matches) > 1:
            raise KeyError(f"hash prefix {prefix!r} is ambiguous: {', '.join(sorted(matches))}")
        return self._run(data, matches[0])

    @staticmethod
    def _run(data: dict[str, Any], h: str) -> Run:
        r = data["runs"][h]
        return Run(h, r["argv"], data["configs"][r["config"]], r.get("config_file"),
                   r.get("note", ""), r.get("recorded", ""), r.get("geometry_version", 1))
