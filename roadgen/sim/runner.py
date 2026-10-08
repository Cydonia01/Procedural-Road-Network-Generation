"""Run esmini on an OpenSCENARIO file and summarize the run."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

import psutil
from lxml import etree

from roadgen.sim.esmini import esmini_binary

log = logging.getLogger(__name__)

PathLike = Union[str, Path]

# "[12.345] [warn] message" (sim time) or "[] [warn] message" (before start).
_LOG_LINE = re.compile(r"^\[(?P<t>[0-9.]*)\]\s*\[(?P<level>\w+)\]\s*(?P<msg>.*)$")
# Missing visual assets only matter for rendering, never for the simulation.
_ASSET_NOISE = re.compile(
    r"3D model .* not located|Failed to load .*\.osgb|Texture file .* not located"
    r"|Error reading file .*\.(osgb|jpg|png)"
)


@dataclass
class SimResult:
    wall_time: float  # [s]
    sim_time: float  # last simulation timestamp in the log [s]
    real_time_factor: float  # sim_time / wall_time
    n_vehicles: int
    errors: list[str]
    log_path: Path
    warnings: list[str] = field(default_factory=list)
    asset_warnings: int = 0  # missing 3D models / textures (ignored)
    returncode: int = 0
    record_path: Optional[Path] = None
    csv_path: Optional[Path] = None
    command: list[str] = field(default_factory=list)
    peak_rss_mb: float = 0.0  # peak resident memory of esmini (+ children)
    killed: Optional[str] = None  # "timeout" or "memory" when the run was stopped
    load_time: Optional[float] = None  # wall time to the first simulation step [s]

    @property
    def step_wall_time(self) -> Optional[float]:
        """Wall time spent stepping (after loading), if the load time is known."""
        return None if self.load_time is None else max(self.wall_time - self.load_time, 0.0)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.errors and self.killed is None


@dataclass
class _Monitored:
    returncode: int
    wall: float
    peak_rss_mb: float
    killed: Optional[str]
    first_match: Optional[float] = None  # wall time when first_line_pattern first appeared


POLL_INTERVAL = 0.02  # [s] memory sampling period
# esmini log lines carry the simulation time once stepping has started.
FIRST_STEP = re.compile(rb"(?:^|\n)\[\d+\.\d+\]")


def _pump(fd: int, out, start: float, pattern: Optional[re.Pattern], state: dict) -> None:
    """Copy pty output to a file, timestamping the first pattern match and EOF."""
    tail = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:  # EIO once the child side is closed
            chunk = b""
        now = time.perf_counter() - start
        if not chunk:
            state["eof"] = now
            return
        out.write(chunk)
        if pattern is not None and state.get("first") is None:
            tail = (tail + chunk)[-8192:]
            if pattern.search(tail):
                state["first"] = now


def _limit_address_space(cap_mb: float):  # pragma: no cover - runs in the child
    """preexec_fn: hard RLIMIT_AS cap where the OS enforces it (Linux)."""
    def apply() -> None:
        import resource

        limit = int(cap_mb * 1024 * 1024)
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ValueError, OSError):
            pass
    return apply


def run_monitored(
    cmd: Sequence[str],
    stdout_path: Path,
    timeout: Optional[float] = None,
    memory_cap_mb: Optional[float] = None,
    first_line_pattern: Optional[re.Pattern] = None,
) -> _Monitored:
    """Run cmd, sampling peak RSS; kill it on timeout or when RSS exceeds the cap.

    Output goes to stdout_path. With first_line_pattern (POSIX only) the child
    writes to a pseudo-terminal, which makes its stdio line-buffered, so the
    moment the pattern first appears is measured as it happens (a plain file
    would be block-buffered). On Linux the memory cap is also set as
    RLIMIT_AS (enforced by the kernel); elsewhere the RSS poll is the cap.
    """
    use_pty = first_line_pattern is not None and hasattr(os, "openpty")
    preexec = (
        _limit_address_space(memory_cap_mb)
        if memory_cap_mb and sys.platform.startswith("linux")
        else None
    )
    state: dict = {}
    with open(stdout_path, "wb") as out:
        if use_pty:
            master, slave = os.openpty()
            start = time.perf_counter()
            proc = subprocess.Popen(cmd, stdout=slave, stderr=slave, stdin=subprocess.DEVNULL,
                                    preexec_fn=preexec)
            os.close(slave)
            reader = threading.Thread(
                target=_pump, args=(master, out, start, first_line_pattern, state), daemon=True
            )
            reader.start()
        else:
            start = time.perf_counter()
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, preexec_fn=preexec)
        ps = psutil.Process(proc.pid)
        sampled, killed = 0, None
        while True:
            # Reap with wait4 ourselves: its rusage gives the kernel's exact peak
            # RSS for this child, even for runs shorter than the poll interval.
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                break
            try:
                rss = ps.memory_info().rss + sum(
                    c.memory_info().rss for c in ps.children(recursive=True)
                )
                sampled = max(sampled, rss)
            except psutil.Error:
                pass  # exiting
            if memory_cap_mb and sampled > memory_cap_mb * 1024 * 1024:
                killed = "memory"
            elif timeout and time.perf_counter() - start > timeout:
                killed = "timeout"
            if killed:
                proc.kill()
                _, status, usage = os.wait4(proc.pid, 0)
                break
            time.sleep(POLL_INTERVAL)
        wall = time.perf_counter() - start
        proc.returncode = os.waitstatus_to_exitcode(status)
        if use_pty:
            reader.join(timeout=5)
            os.close(master)
            # EOF on the pty is seen when the child exits; it is more precise
            # than the reap above, which only happens every POLL_INTERVAL.
            if "eof" in state:
                wall = min(wall, state["eof"])
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    maxrss = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    peak = max(sampled, maxrss)
    return _Monitored(proc.returncode, wall, peak / 2**20, killed, state.get("first"))


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
    timestep: float = 0.05,
    record: bool = True,
    csv: bool = True,
    out_dir: Optional[PathLike] = None,
    extra_args: Sequence[str] = (),
    timeout: Optional[float] = None,
    memory_cap_mb: Optional[float] = None,
) -> SimResult:
    """Run esmini and return timing, peak memory and parsed problems.

    Outputs (<stem>.dat, <stem>.csv, <stem>.log, <stem>.stdout) go next to
    the scenario unless out_dir is given. A run exceeding timeout [s] or
    memory_cap_mb is killed and reported via SimResult.killed, not raised.
    """
    xosc_path = Path(xosc_path).resolve()
    out = Path(out_dir).resolve() if out_dir else xosc_path.parent
    out.mkdir(parents=True, exist_ok=True)
    stem = xosc_path.stem
    log_path = out / f"{stem}.log"
    record_path = out / f"{stem}.dat" if record else None
    csv_path = out / f"{stem}.csv" if csv else None

    cmd = [str(esmini_binary("esmini")), "--osc", str(xosc_path)]
    cmd += ["--headless"] if headless else ["--window", "60", "60", "1280", "800"]
    cmd += ["--fixed_timestep", str(timestep), "--logfile_path", str(log_path)]
    if record_path:
        cmd += ["--record", str(record_path)]
    if csv_path:
        cmd += ["--csv_logger", str(csv_path)]
    if has_scenegraph(xosc_path):
        # With a SceneGraphFile esmini would otherwise skip generating the roads.
        cmd.append("--enforce_generate_model")
    cmd += list(extra_args)

    log.info("running %s", " ".join(cmd))
    stdout_path = out / f"{stem}.stdout"
    if log_path.exists():
        os.remove(log_path)  # don't parse a previous run's log
    run = run_monitored(cmd, stdout_path, timeout, memory_cap_mb, first_line_pattern=FIRST_STEP)
    wall = run.wall

    stdout = stdout_path.read_text(errors="replace")
    text = log_path.read_text(errors="replace") if log_path.is_file() else stdout
    sim_time, errors, warnings, assets = parse_log(text)
    if run.killed:
        limit = f"{timeout} s" if run.killed == "timeout" else f"{memory_cap_mb} MB"
        errors.append(f"esmini killed: {run.killed} limit ({limit}) exceeded")
    elif run.returncode != 0:
        tail = stdout.strip().splitlines()[-5:]
        errors.append(f"esmini exited with code {run.returncode}: " + " | ".join(tail))
    return SimResult(
        wall_time=wall,
        sim_time=sim_time,
        real_time_factor=sim_time / wall if wall > 0 else 0.0,
        n_vehicles=count_vehicles(xosc_path),
        errors=errors,
        log_path=log_path,
        warnings=warnings,
        asset_warnings=assets,
        returncode=run.returncode,
        record_path=record_path,
        csv_path=csv_path,
        command=cmd,
        peak_rss_mb=run.peak_rss_mb,
        killed=run.killed,
        load_time=run.first_match,
    )
