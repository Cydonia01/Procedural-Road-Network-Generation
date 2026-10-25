"""Run folders: every successful CLI command in out/<hash>/, with its outputs.

A run is defined by its *spec*: the command, the arguments that differ from
their defaults, and the configuration that differs from roadgen/defaults.yaml
(--config, --preset, --params, --set and the parameter flags folded in, see
cli.compile_spec). The hash is a hash of the spec, so the same inputs,
however they were typed, give the same hash and the same folder::

    out/<hash>/
      run.yaml        # hash, note, recorded, cli + the spec (command, args, config)
      <outputs>       # everything the command wrote; -o only names them

A command runs in a temporary folder next to the final one, which becomes
out/<hash>/ only if it succeeds: a failed command leaves nothing behind.
Running the same spec again replaces the folder. Replay (`python -m cli
HASH`, or `python -m cli run out/<hash>/run.yaml`) runs the spec against the
current defaults and code.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import yaml

HASH_LENGTH = 12  # hex digits of sha256 kept
MIN_PREFIX = 4  # shortest hash prefix accepted by lookup
ENV_VAR = "ROADGEN_HISTORY"  # run folder root; "" or "off" disables recording
DISABLED = ("", "off", "none", "0")
RUN_FILE = "run.yaml"
SPEC_KEYS = ("command", "args", "config")
RUN_DIR = re.compile(rf"[0-9a-f]{{{HASH_LENGTH}}}")

PathLike = Union[str, Path]


def plain(value: Any) -> Any:
    """JSON-ready copy: tuples become lists."""
    return json.loads(json.dumps(value, default=list))


def digest(value: Any, length: int = HASH_LENGTH) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def run_hash(spec: dict[str, Any]) -> str:
    return digest({k: spec.get(k) or ([] if k == "command" else {}) for k in SPEC_KEYS})


def diff(config: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """The part of config that differs from base (nested mappings key by key)."""
    out = {}
    for key, value in config.items():
        ref = base.get(key)
        if isinstance(value, dict) and isinstance(ref, dict):
            sub = diff(value, ref)
            if sub:
                out[key] = sub
        elif key not in base or plain(value) != plain(ref):
            out[key] = plain(value)
    return out


@dataclass(frozen=True)
class Run:
    hash: str
    path: Path  # the run folder
    spec: dict[str, Any]  # command, args, config
    note: str
    recorded: str
    cli: str  # the command as typed

    def command_line(self) -> str:
        return self.cli or " ".join(["python -m cli", *self.spec["command"]])


class RunStore:
    """The run folders under root (default out/)."""

    def __init__(self, root: PathLike) -> None:
        self.root = Path(root)

    @classmethod
    def from_env(cls, default: Optional[str]) -> Optional[RunStore]:
        """The runs at $ROADGEN_HISTORY or default; None if recording is off."""
        root = os.environ.get(ENV_VAR, default)
        if root is None or root.strip().lower() in DISABLED:
            return None
        return cls(root)

    def begin(self, h: str) -> Path:
        """An empty temporary folder for run h, at the same depth as its final folder
        (relative paths between outputs stay valid when it is renamed)."""
        tmp = self.root / f".tmp-{h}-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        return tmp

    @staticmethod
    def discard(tmp: Path) -> None:
        shutil.rmtree(tmp, ignore_errors=True)

    def commit(self, h: str, tmp: Path, spec: dict[str, Any], note: Optional[str] = None,
               cli: Optional[str] = None) -> Path:
        """Write run.yaml and move tmp to root/h (replacing an earlier run of h, whose
        note is kept unless a new one is given)."""
        final = self.root / h
        if not note and (final / RUN_FILE).is_file():
            note = load_run(final / RUN_FILE).note
        record = {
            "hash": h,
            "note": note or "",
            "recorded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "cli": cli or "",
            **{k: plain(spec.get(k) or ([] if k == "command" else {})) for k in SPEC_KEYS},
        }
        (tmp / RUN_FILE).write_text(
            yaml.safe_dump(record, sort_keys=False, default_flow_style=None, allow_unicode=True,
                           width=100))
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)
        return final

    def runs(self) -> list[Run]:
        """All runs, oldest first."""
        if not self.root.is_dir():
            return []
        paths = [p for p in self.root.iterdir()
                 if RUN_DIR.fullmatch(p.name) and (p / RUN_FILE).is_file()]
        runs = [load_run(p / RUN_FILE) for p in paths]
        return sorted(runs, key=lambda r: (r.recorded, (r.path / RUN_FILE).stat().st_mtime_ns))

    def get(self, prefix: str) -> Run:
        """The run whose hash starts with prefix (at least MIN_PREFIX digits, unique)."""
        prefix = prefix.lower()
        if len(prefix) < MIN_PREFIX:
            raise KeyError(f"hash prefix {prefix!r} is too short (need {MIN_PREFIX}+ digits)")
        matches = sorted(p for p in (self.root.iterdir() if self.root.is_dir() else [])
                         if RUN_DIR.fullmatch(p.name) and p.name.startswith(prefix)
                         and (p / RUN_FILE).is_file())
        if not matches:
            raise KeyError(f"no run {prefix!r} in {self.root}")
        if len(matches) > 1:
            raise KeyError(f"hash prefix {prefix!r} is ambiguous: {', '.join(p.name for p in matches)}")
        return load_run(matches[0] / RUN_FILE)


def load_spec(path: PathLike) -> tuple[dict[str, Any], dict[str, Any]]:
    """(spec, the whole file) from a run.yaml or a hand-written file of the same form."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(data, dict) or not data.get("command"):
        raise ValueError(f"{path}: expected a mapping with a 'command' list")
    command = data["command"]
    spec = {
        "command": command.split() if isinstance(command, str) else list(command),
        "args": data.get("args") or {},
        "config": data.get("config") or {},
    }
    return spec, data


def load_run(path: Path) -> Run:
    spec, data = load_spec(path)
    return Run(path.parent.name, path.parent, spec, data.get("note") or "",
               str(data.get("recorded") or ""), data.get("cli") or "")
