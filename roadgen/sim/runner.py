"""Run esmini on an OpenSCENARIO file (to look at a network with traffic) and
read the problems from its log."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

from lxml import etree

from roadgen.config import DEFAULTS
from roadgen.sim.esmini import esmini_binary

log = logging.getLogger(__name__)

PathLike = Union[str, Path]

# "[12.345] [warn] message" (sim time) or "[] [warn] message" (before start).
_LOG_LINE = re.compile(r"^\[(?P<t>[0-9.]*)\]\s*\[(?P<level>\w+)\]\s*(?P<msg>.*)$")
# Missing visual assets only matter for rendering, never for the scenario.
_ASSET_NOISE = re.compile(
    r"3D model .* not located|Failed to load .*\.osgb|Texture file .* not located"
    r"|Error reading file .*\.(osgb|jpg|png)"
)


@dataclass
class SimResult:
    sim_time: float  # last simulation timestamp in the log [s]
    n_vehicles: int
    errors: list[str]
    log_path: Path
    warnings: list[str] = field(default_factory=list)
    asset_warnings: int = 0  # missing 3D models / textures (ignored)
    returncode: int = 0
    command: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.errors


def parse_log(text: str) -> tuple[float, list[str], list[str], int]:
    """(last sim time, errors, warnings, ignored asset warnings) from an esmini log."""
    sim_time, errors, warnings, assets = 0.0, [], [], 0
    for line in text.splitlines():
        m = _LOG_LINE.match(line.strip())
        if not m:
            continue
        if m["t"]:
            sim_time = max(sim_time, float(m["t"]))
        level, msg = m["level"].lower(), m["msg"].strip()
        if level not in ("warn", "warning", "error"):
            continue
        if _ASSET_NOISE.search(msg):
            assets += 1
        elif level == "error":
            errors.append(line.strip())
        else:
            warnings.append(line.strip())
    return sim_time, errors, warnings, assets


def count_vehicles(xosc_path: PathLike) -> int:
    return len(etree.parse(str(xosc_path)).getroot().findall("Entities/ScenarioObject"))


def has_scenegraph(xosc_path: PathLike) -> bool:
    return etree.parse(str(xosc_path)).getroot().find("RoadNetwork/SceneGraphFile") is not None


def run_esmini(
    xosc_path: PathLike,
    headless: bool = True,
    timestep: float = DEFAULTS["esmini"]["timestep"],
    out_dir: Optional[PathLike] = None,
    extra_args: Sequence[str] = (),
    timeout: Optional[float] = None,
    window: Sequence[int] = DEFAULTS["esmini"]["window"],
) -> SimResult:
    """Run esmini on a scenario and return the problems its log reports.

    The log (<stem>.log) and esmini's console output (<stem>.stdout) go next
    to the scenario unless out_dir is given. A run longer than timeout [s] is
    stopped and reported as an error.
    """
    xosc_path = Path(xosc_path).resolve()
    out = Path(out_dir).resolve() if out_dir else xosc_path.parent
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / f"{xosc_path.stem}.log"
    stdout_path = out / f"{xosc_path.stem}.stdout"

    cmd = [str(esmini_binary("esmini")), "--osc", str(xosc_path)]
    cmd += ["--headless"] if headless else ["--window", *map(str, window)]
    cmd += ["--fixed_timestep", str(timestep), "--logfile_path", str(log_path)]
    if has_scenegraph(xosc_path):
        # With a SceneGraphFile esmini would otherwise skip generating the roads.
        cmd.append("--enforce_generate_model")
    cmd += list(extra_args)

    log.info("running %s", " ".join(cmd))
    if log_path.exists():
        os.remove(log_path)  # don't parse a previous run's log
    returncode, stopped = 0, False
    with open(stdout_path, "wb") as stdout:
        try:
            returncode = subprocess.run(cmd, stdout=stdout, stderr=subprocess.STDOUT, timeout=timeout,
                                        check=False).returncode
        except subprocess.TimeoutExpired:
            stopped = True
    console = stdout_path.read_text(errors="replace")
    text = log_path.read_text(errors="replace") if log_path.is_file() else console
    sim_time, errors, warnings, assets = parse_log(text)
    if stopped:
        errors.append(f"esmini stopped after the {timeout} s time limit")
    elif returncode != 0:
        tail = console.strip().splitlines()[-5:]
        errors.append(f"esmini exited with code {returncode}: " + " | ".join(tail))
    return SimResult(sim_time=sim_time, n_vehicles=count_vehicles(xosc_path), errors=errors,
                     log_path=log_path, warnings=warnings, asset_warnings=assets,
                     returncode=returncode, command=cmd)
