"""Still images of a network (or a running scenario) rendered by esmini.

odrviewer has no camera choice, so esmini renders instead: an .xodr gets a
scenario with no vehicles that only waits until time `at`, a given .xosc
runs as it is. The camera is a fixed custom camera: by default straight
down over the middle of the network, high enough to see all of it; or any
pose x, y, z[, heading, pitch]. Frames come from esmini's --capture_screen
(one .tga per frame); the last one is kept as a PNG.

Without a display (the dev container) esmini runs under xvfb-run.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Optional, Union

from lxml import etree

from roadgen.sim.esmini import esmini_binary

log = logging.getLogger(__name__)

PathLike = Union[str, Path]

TIMESTEP = 0.1  # [s] between captured frames
TOP_PITCH = math.pi / 2  # looking straight down
VIEW_WIDTH_PER_HEIGHT = 0.8  # ground width esmini shows per metre of camera height (measured)
MARGIN = 1.2  # room around the network in a top view

_STILL = """<?xml version="1.0" encoding="utf-8"?>
<OpenSCENARIO>
  <FileHeader revMajor="1" revMinor="2" date="1970-01-01T00:00:00" description="still" author="roadgen"/>
  <ParameterDeclarations/>
  <CatalogLocations/>
  <RoadNetwork><LogicFile filepath="{xodr}"/>{scenegraph}</RoadNetwork>
  <Entities/>
  <Storyboard>
    <Init><Actions/></Init>
    <StopTrigger><ConditionGroup><Condition name="end" delay="0" conditionEdge="none">
      <ByValueCondition><SimulationTimeCondition value="{until}" rule="greaterThan"/></ByValueCondition>
    </Condition></ConditionGroup></StopTrigger>
  </Storyboard>
</OpenSCENARIO>
"""


def network_bounds(xodr_path: PathLike) -> tuple[float, float, float, float]:
    """(x0, x1, y0, y1) of the plan-view geometry: start points and straight-line ends."""
    xs, ys = [], []
    for g in etree.parse(str(xodr_path)).getroot().iter("geometry"):
        x, y, hdg, length = (float(g.get(k)) for k in ("x", "y", "hdg", "length"))
        xs += [x, x + length * math.cos(hdg)]
        ys += [y, y + length * math.sin(hdg)]
    if not xs:
        raise ValueError(f"{xodr_path}: no road geometry")
    return min(xs), max(xs), min(ys), max(ys)


def top_camera(bounds: tuple[float, float, float, float], size: tuple[int, int]) -> tuple[float, ...]:
    """Camera (x, y, z, heading, pitch) looking straight down on the whole of bounds."""
    x0, x1, y0, y1 = bounds
    aspect = size[0] / size[1]
    width = max(x1 - x0, (y1 - y0) * aspect, 20.0) * MARGIN
    return ((x0 + x1) / 2, (y0 + y1) / 2, width / VIEW_WIDTH_PER_HEIGHT, 0.0, TOP_PITCH)


def still_xosc(xodr_path: PathLike, path: PathLike, until: float,
               scenegraph: Optional[PathLike] = None) -> Path:
    """A scenario without vehicles on xodr_path that ends after `until` seconds."""
    sg = f'<SceneGraphFile filepath="{Path(scenegraph).resolve()}"/>' if scenegraph else ""
    path = Path(path)
    path.write_text(_STILL.format(xodr=Path(xodr_path).resolve(), scenegraph=sg, until=until))
    return path


def screenshot(png: PathLike, xodr: Optional[PathLike] = None, xosc: Optional[PathLike] = None,
               at: float = 0.3, camera: Optional[Sequence[float]] = None,
               scenegraph: Optional[PathLike] = None, size: tuple[int, int] = (1280, 800),
               timeout: float = 300.0) -> Path:
    """Render one frame at simulation time `at` [s] to png and return its path.

    Give xodr (a still scenario is written) or xosc (run as it is; it must
    last at least `at`). camera: x, y, z[, heading, pitch] [m, rad]; default
    a top view of the whole network (needs xodr, or the xosc's LogicFile).
    """
    if (xodr is None) == (xosc is None):
        raise ValueError("give either xodr or xosc")
    png = Path(png)
    png.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shot_", dir=png.parent) as tmp:
        work = Path(tmp)
        if xodr is not None:
            scenario = still_xosc(xodr, work / "still.xosc", at + 2 * TIMESTEP, scenegraph)
            network = Path(xodr)
        else:
            scenario = Path(xosc).resolve()
            network = _logic_file(scenario)
        if camera is None:
            camera = top_camera(network_bounds(network), size)
        cmd = [str(esmini_binary("esmini")), "--osc", str(scenario), "--window", "0", "0", *map(str, size),
               "--fixed_timestep", str(TIMESTEP), "--camera_mode", "custom",
               "--custom_fixed_camera", ",".join(f"{v:.3f}" for v in camera), "--capture_screen"]
        if scenegraph is not None:  # esmini otherwise draws the scene file instead of the roads
            cmd.append("--enforce_generate_model")
        if not os.environ.get("DISPLAY"):
            if not shutil.which("xvfb-run"):
                raise RuntimeError("no DISPLAY and no xvfb-run: cannot render a screenshot")
            screen = f"-screen 0 {size[0] + 100}x{size[1] + 100}x24 +extension GLX"
            cmd = ["xvfb-run", "-a", "-s", screen, *cmd]
        log.debug("running %s", " ".join(cmd))
        done = subprocess.run(cmd, check=False, cwd=work, capture_output=True, text=True, timeout=timeout)
        frames = sorted(work.glob("screen_shot_*.tga"))
        if done.returncode != 0 or not frames:
            raise RuntimeError(f"esmini wrote no frame (exit {done.returncode}): {done.stdout[-500:]}")
        frame = frames[min(len(frames) - 1, round(at / TIMESTEP))]
        from PIL import Image  # matplotlib's dependency; only needed here

        Image.open(frame).save(png)
    log.info("wrote %s", png)
    return png


def _logic_file(xosc: Path) -> Path:
    el = etree.parse(str(xosc)).getroot().find("RoadNetwork/LogicFile")
    if el is None:
        raise ValueError(f"{xosc}: no RoadNetwork/LogicFile")
    path = Path(el.get("filepath"))
    return path if path.is_absolute() else (xosc.parent / path).resolve()
