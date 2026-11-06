"""Junction surface in the scene (stage R5): the parts of a junction no lane covers.

The OpenDRIVE builder finds them (holes in the union of a junction's lanes,
OpenDriveBuilder.fill_areas, listed in the sidecar as "junction_fills") and
could write them as <object type="patch">. esmini draws such outline objects
in a plain colour, lighter than its textured lanes, so a junction looked
patched. With a scene file the pipeline leaves the patches out and draws the
areas here instead: flat triangles on the junction plane, ``below`` under the
lanes (so lane edges that overlap them win), in the colour of esmini's
asphalt texture (mean of resources/models/asphalt.jpg). Nothing in the
simulation reads them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import numpy as np
import shapely
from shapely.geometry import Polygon

from roadgen.config import DEFAULTS, section
from roadgen.scene.osgt import Mesh

log = logging.getLogger(__name__)

_D = section(DEFAULTS, "scene")
Outline = list[tuple[float, float, float]]


@dataclass(frozen=True)
class SceneParams:
    junction_fill: bool = _D["junction_fill"]  # draw the junction surface no lane covers
    asphalt_color: tuple[float, float, float] = _D["asphalt_color"]  # RGB in [0, 1]
    fill_below: float = _D["fill_below"]  # the fill sits this far under the lanes [m]

    def __post_init__(self) -> None:
        if self.fill_below < 0 or not all(0.0 <= c <= 1.0 for c in self.asphalt_color):
            raise ValueError("need fill_below >= 0 and asphalt_color in [0, 1]")


def read_fills(sidecar: Union[str, Path]) -> list[Outline]:
    """The junction fill outlines of a sidecar (.roadmap.json); [] without any."""
    data = json.loads(Path(sidecar).read_text())
    return [[tuple(p) for p in f["outline"]] for f in data.get("junction_fills", [])]


def junction_fill_mesh(fills: list[Outline], params: SceneParams = SceneParams()) -> Mesh:
    """Triangles covering each outline (constrained Delaunay), heights from its corners
    (all on the junction plane, so linear in x and y), facing up."""
    tris, normals = [], []
    for outline in fills:
        if len(outline) < 3:
            continue
        xy = np.array([(x, y) for x, y, _ in outline])
        z = np.array([p[2] for p in outline]) - params.fill_below
        poly = Polygon(xy)
        if not poly.is_valid:
            poly = poly.buffer(0)
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
        # The plane through the corners (least squares; exact for a planar outline).
        a = np.column_stack([xy, np.ones(len(xy))])
        coef = np.linalg.lstsq(a, z, rcond=None)[0]
        for tri in shapely.constrained_delaunay_triangles(poly).geoms:
            pts = list(tri.exterior.coords)[:3]
            if Polygon(pts).area < 1e-6:
                continue
            if shapely.is_ccw(tri.exterior) is False:
                pts = pts[::-1]
            corners = tuple((float(x), float(y), float(coef[0] * x + coef[1] * y + coef[2])) for x, y in pts)
            (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = corners
            n = np.cross([x1 - x0, y1 - y0, z1 - z0], [x2 - x0, y2 - y0, z2 - z0])
            n = n / (np.linalg.norm(n) or 1.0)
            if n[2] < 0:  # face up
                corners, n = corners[::-1], -n
            tris.append(corners)
            normals.append(tuple(float(v) for v in n))
    log.info("junction fill: %d areas, %d triangles", len(fills), len(tris))
    return Mesh("junction_fill", tris, normals, tuple(params.asphalt_color))
