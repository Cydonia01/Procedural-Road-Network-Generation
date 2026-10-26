"""L-system-style road growth after Parish & Müller (2001), "Procedural
Modeling of Cities".

Road segments are proposed into a priority queue (lower time = earlier).
Each popped proposal passes local constraints, which may shorten, redirect
or reject it:

1. bounds: proposals ending outside the area are dropped;
2. intersect: a proposal crossing an existing road stops at the first
   crossing, which becomes a junction (the crossed road is split there);
3. snap: an end within snap_radius of an existing node ends on that node;
   an end within snap_radius of a road ends on that road (split);
4. minimum length, no duplicate road, and a minimum angle to every road
   already at either end.

An accepted segment with a free end asks the global goal for follow-ups.
The "grid" goal proposes a straight continuation (time + 1) and, with
branch_prob, perpendicular branches (time + branch_delay). Headings are
pulled back to the grid axes every step, so angle_jitter_deg never drifts.

The "field" goal (stage C2) follows a blend of street patterns instead
(roadgen.generators.patterns: grid, radial, organic, contour, suburb, mixed by
pattern_weights or a style control map): avenues follow the field's major
direction, streets its minor one, each segment stepping along it like a
streamline (midpoint rule), turning at most as far as its design radius
allows; branches leave along the other direction. Block lengths and the
branch probability come from the patterns too. At a radial centre that is
the seed point, growth starts with radial_spokes avenues (a star, Paris).

With input maps (a MapSet of terrain / elevation / population, normally
built as one pipeline so they agree: water in the valleys, nobody living on
it), growth follows the paper more closely:

- phase 1, highways (given a population map): from the seed, highways
  grow in the two most populated directions. At every end a fan of rays
  samples the population, weighted by inverse distance; the best direction
  that passes the map constraints wins. Highways may cross water up to
  max_bridge_length (the edge is listed in metadata["bridges"]), branch
  into new highways, and spawn grid streets on both sides;
- phase 2, streets: grid growth from the seed and from those spawn points.
  A street running into water (or vegetation) is pruned to the shore if at
  least prune_factor of it is left, and then only branches; streets stop
  where the population density is below street_min_density;
- every road keeps its grade below max_grade on the elevation map.

Without maps the result is exactly the plain grid growth.

Curved streets (curve_mode, stage A9; "none" keeps the straight grid
exactly): with "drift" a street's heading follows a mean-reverting random
walk around its grid axis (curve_drift_deg per segment, curve_reversion back
towards the axis), each change limited so that the bend between two blocks
can still get its design radius; branches leave perpendicular to the
street. "contour" also pulls the heading towards the terrain's contour
direction, weighted by the slope (Parish & Mueller's San Francisco rule).
Afterwards every street between two nodes gets a bow node so that its
length / chord reaches curve_tortuosity (Aliaga et al.), where the bow
stays legal. The OpenDRIVE builder turns the resulting bend nodes into
smooth curves.

Manhattan-style grids (stage C1): oneway_pattern "alternating" makes
every second avenue (or street) one-way the other way, all lanes forward,
like Manhattan's avenues; each grid line keeps its index from where it
started. boundary "ring" extends every road that reaches the edge of the
area straight out to a two-way ring road around it, so one-way grids stay
strongly connected. edge_drop_prob drops street segments at random where
every travel direction stays possible (roadgen.generators.transforms.drop).

Connectivity and cul-de-sacs (stage C4): with detour_ratio k > 0, a
connector pass after growth joins nearby nodes whose trip over the network
is more than k times their straight distance (Lechner et al. 2003, sec. 3):
k near 1 closes almost every gap (grid-like, many loops), a large k leaves a
tree of dead ends (suburbs). Dead ends of streets then become cul-de-sac
bulbs with culdesac_prob, or in suburbs with suburb_culdesac_prob
(roadgen.generators.transforms.culdesac), so traffic can turn around.

Everything is computed in the area's local frame (lower-left corner at 0,
x along the grid's first axis) and rotated/translated to world coordinates
at the end. Output is deterministic for a seed.
"""

from __future__ import annotations

import heapq
import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterator, Literal, Optional

import numpy as np

from roadgen.config import DEFAULTS, section
from roadgen.generators.base import RoadNetworkGenerator, assign_node_kinds, register
from roadgen.generators.patterns import PATTERNS, RADIAL, SUBURB, FieldParams, StreetField
from roadgen.generators.repair import repair_short_edges
from roadgen.generators.transforms.culdesac import add_culdesacs
from roadgen.generators.transforms.drop import drop_edges
from roadgen.generators.transforms.roundabout import add_roundabouts
from roadgen.generators.transforms.stagger import stagger_crossings
from roadgen.geometry import design
from roadgen.geometry.design import DesignParams
from roadgen.graph.model import Edge, Node, RoadGraph
from roadgen.maps.model import (
    STYLE_CLASSES,
    STYLE_RADIAL,
    VEGETATION,
    WATER,
    Bounds,
    MapLayer,
    MapSet,
    bilinear,
)

log = logging.getLogger(__name__)

GlobalGoal = Literal["grid", "field"]
GLOBAL_GOALS = ("grid", "field")
CurveMode = Literal["none", "drift", "contour"]
CURVE_MODES = ("none", "drift", "contour")
OnewayPattern = Literal["none", "alternating"]
ONEWAY_PATTERNS = ("none", "alternating")
BoundaryMode = Literal["none", "ring"]
BOUNDARY_MODES = ("none", "ring")
SALT_DROP = 0x6472  # random sub-stream of edge_drop_prob, independent of the growth's
SALT_FIELD = 0x6669  # random sub-stream of the pattern field's noise
SALT_CONNECT = 0x636f  # random sub-stream of the connector pass
SALT_CULDESAC = 0x6375  # random sub-stream of the cul-de-sac bulbs
SALT_STAGGER = 0x7374  # random sub-stream of the staggered crossings
SALT_ROUNDABOUT = 0x726f  # random sub-stream of the roundabouts
CONNECT_CANDIDATES = 6  # nearest nodes a connector tries from each node
CONTOUR_SLOPE = 0.08  # terrain grade at which contour streets follow the contour fully
CONTOUR_MAX_DEG = 60.0  # contour streets turn at most this far from their grid axis
GRADIENT_STEP = 10.0  # [m] finite-difference step for the terrain gradient
Point = tuple[float, float]
EPS = 1e-9
# Road type per edge / proposal ("axis" for grid roads).
STREET_X, AVENUE_Y, HIGHWAY, RING = 0, 1, 2, 3
SEED_RAYS = 16  # directions tried for the first two highways
GRADE_BASELINE = 25.0  # [m] grade is measured over this distance; roads smooth out smaller bumps

_D = section(DEFAULTS, "generators", "lsystem")


@dataclass(frozen=True)
class LSystemParams:
    global_goal: GlobalGoal = _D["global_goal"]
    extent: tuple[float, float] = _D["extent"]  # area width (local x), height (local y) [m]
    origin: tuple[float, float] = _D["origin"]  # world position of the area's lower-left corner
    rotation_deg: float = _D["rotation_deg"]  # rotation of the local frame around origin
    start: tuple[float, float] = _D["start"]  # seed point, as a fraction of the extent
    block_size: tuple[float, float] = _D["block_size"]  # grid goal: segment length along local x / y
    branch_prob: float = _D["branch_prob"]  # chance of each perpendicular branch at a free end
    branch_delay: int = _D["branch_delay"]  # queue delay of branches relative to straight growth
    angle_jitter_deg: float = _D["angle_jitter_deg"]  # std dev of heading noise around the grid axes
    length_jitter: float = _D["length_jitter"]  # relative std dev of segment length
    snap_radius: float = _D["snap_radius"]  # [m]
    min_segment_length: float = _D["min_segment_length"]  # [m]
    min_angle_deg: float = _D["min_angle_deg"]  # min angle between roads meeting at a node
    max_segments: int = _D["max_segments"]
    # Roads along local y are "avenues" (major), along local x "streets" (minor).
    major_lanes: tuple[int, int] = _D["major_lanes"]
    minor_lanes: tuple[int, int] = _D["minor_lanes"]
    lane_width: float = _D["lane_width"]
    major_speed: float = _D["major_speed"]
    minor_speed: float = _D["minor_speed"]
    # --- input maps; ignored without maps -----------------------------------------------
    highways: bool = _D["highways"]  # grow highways first (needs a population map)
    highway_length: float = _D["highway_length"]  # [m] per segment
    highway_rays: int = _D["highway_rays"]  # directions tried per highway end
    highway_max_turn_deg: float = _D["highway_max_turn_deg"]  # fan half-angle per segment
    highway_max_detour_deg: float = _D["highway_max_detour_deg"]  # wider fan if none fits
    highway_ray_length: float = _D["highway_ray_length"]  # [m] how far the rays look
    highway_ray_samples: int = _D["highway_ray_samples"]  # population samples per ray
    highway_branch_prob: float = _D["highway_branch_prob"]  # per side, per segment
    highway_branch_delay: int = _D["highway_branch_delay"]
    highway_min_density: float = _D["highway_min_density"]  # [people/km^2] highways stop below
    highway_lanes: tuple[int, int] = _D["highway_lanes"]
    highway_speed: float = _D["highway_speed"]
    max_bridge_length: float = _D["max_bridge_length"]  # [m] of water a highway may cross
    street_min_density: float = _D["street_min_density"]  # [people/km^2] streets stop below
    avoid_vegetation: bool = _D["avoid_vegetation"]  # streets stay out of parks / forest
    prune_factor: float = _D["prune_factor"]  # min share of a street left after pruning
    max_grade: float = _D["max_grade"]  # max rise / run along a road; 0 = no limit
    # --- curved streets (stage A9); "none" keeps the straight grid exactly -------------
    curve_mode: CurveMode = _D["curve_mode"]  # none | drift | contour
    curve_drift_deg: float = _D["curve_drift_deg"]  # std dev of a street's heading change per segment
    curve_reversion: float = _D["curve_reversion"]  # pull back towards the grid axis per segment, 0-1
    curve_tortuosity: float = _D["curve_tortuosity"]  # target length / chord of a street; 1 = straight
    # --- Manhattan-style grids (stage C1) ------------------------------------------------
    oneway_pattern: tuple[OnewayPattern, OnewayPattern] = _D["oneway_pattern"]  # (avenues, streets)
    boundary: BoundaryMode = _D["boundary"]  # none | ring: two-way ring road around the area
    ring_offset: float = _D["ring_offset"]  # [m] from the edge of the area to the ring
    ring_lanes: tuple[int, int] = _D["ring_lanes"]
    edge_drop_prob: float = _D["edge_drop_prob"]  # chance to drop each street segment
    # --- pattern fields (stage C2), global_goal "field" ---------------------------------
    pattern_weights: tuple[float, ...] = _D["pattern_weights"]  # grid, radial, organic, contour, suburb
    grid_angle_deg: float = _D["grid_angle_deg"]  # grid pattern's turn from the local axes
    radial_center: tuple[float, float] = _D["radial_center"]  # fraction of the extent
    radial_radius: float = _D["radial_radius"]  # [m] Gaussian falloff of the radial weight; 0 = none
    radial_spokes: int = _D["radial_spokes"]  # avenues leaving a radial centre at the seed point
    organic: float = _D["organic"]  # grid-to-organic knob, 0-1
    organic_max_deg: float = _D["organic_max_deg"]  # largest turn of the organic pattern
    organic_scale: float = _D["organic_scale"]  # [m] size of the organic pattern's bends
    skew_deg: float = _D["skew_deg"]  # largest turn of streets away from 90 deg to the avenues
    skew_scale: float = _D["skew_scale"]  # [m] size of the skew field's patches
    snap_deg: float = _D["snap_deg"]  # at organic 0, headings this close to the grid snap to it
    suburb_block_size: tuple[float, float] = _D["suburb_block_size"]  # block_size in suburbs
    suburb_branch_prob: float = _D["suburb_branch_prob"]  # branch_prob in suburbs
    suburb_end_prob: float = _D["suburb_end_prob"]  # chance a suburb street ends at a node
    style_blend_width: float = _D["style_blend_width"]  # [m] neighbouring districts blend over this
    # --- connectivity and cul-de-sacs (stage C4) -----------------------------------------
    detour_ratio: float = _D["detour_ratio"]  # connect where the network trip > this x straight; 0 = off
    connect_radius: float = _D["connect_radius"]  # [m] how far a connector may reach
    culdesac_prob: float = _D["culdesac_prob"]  # chance of a bulb at a street's dead end
    suburb_culdesac_prob: float = _D["suburb_culdesac_prob"]  # ... in suburbs (field goal)
    culdesac_radius: float = _D["culdesac_radius"]  # [m] of the bulb's loop (centre line)
    culdesac_speed: float = _D["culdesac_speed"]  # [m/s] on the loop
    # --- junction variety (stage C5) -----------------------------------------------------
    stagger_prob: float = _D["stagger_prob"]  # chance a 4-way crossing becomes two offset T junctions
    stagger_offset: float = _D["stagger_offset"]  # [m] between the two T junctions
    max_degree: int = _D["max_degree"]  # most roads the repair may join at one node
    # --- roundabouts (stage C7) ----------------------------------------------------------
    roundabout_prob: float = _D["roundabout_prob"]  # chance a 3-5-way junction becomes a roundabout
    roundabout_radius: float = _D["roundabout_radius"]  # [m] single-lane ring (centre line)
    roundabout_radius_avenue: float = _D["roundabout_radius_avenue"]  # [m] two-lane ring
    roundabout_speed: float = _D["roundabout_speed"]  # [m/s] on the ring

    def __post_init__(self) -> None:
        if self.global_goal not in GLOBAL_GOALS:
            raise ValueError(f"unknown global_goal {self.global_goal!r}; available: {GLOBAL_GOALS}")
        if min(self.extent) <= 0 or min(self.block_size) <= 0:
            raise ValueError("extent and block_size must be positive")
        if not all(0.0 <= f <= 1.0 for f in self.start):
            raise ValueError("start is a fraction of the extent, each in [0, 1]")
        if not 0.0 <= self.branch_prob <= 1.0:
            raise ValueError("branch_prob must be in [0, 1]")
        if self.snap_radius < 0 or self.min_segment_length <= 0:
            raise ValueError("snap_radius must be >= 0 and min_segment_length > 0")
        if self.snap_radius * 2 >= min(self.block_size):
            raise ValueError("snap_radius must be well below the block size")
        if not 0.0 < self.min_angle_deg < 90.0:
            raise ValueError("min_angle_deg must be in (0, 90)")
        for lanes in (self.major_lanes, self.minor_lanes, self.highway_lanes, self.ring_lanes):
            if len(lanes) != 2 or min(lanes) < 0 or sum(lanes) < 1:
                raise ValueError(f"invalid lane counts {lanes}")
        if self.highway_length < self.min_segment_length or self.highway_ray_length <= 0:
            raise ValueError("highway_length must be >= min_segment_length, highway_ray_length > 0")
        if self.highway_rays < 1 or self.highway_ray_samples < 1:
            raise ValueError("highway_rays and highway_ray_samples must be >= 1")
        if not 0.0 <= self.highway_max_turn_deg <= self.highway_max_detour_deg < 90.0:
            raise ValueError("need 0 <= highway_max_turn_deg <= highway_max_detour_deg < 90")
        if not 0.0 <= self.highway_branch_prob <= 1.0 or not 0.0 <= self.prune_factor <= 1.0:
            raise ValueError("highway_branch_prob and prune_factor must be in [0, 1]")
        if min(self.max_bridge_length, self.highway_min_density, self.street_min_density,
               self.max_grade) < 0:
            raise ValueError("max_bridge_length, densities and max_grade must be >= 0")
        if self.curve_mode not in CURVE_MODES:
            raise ValueError(f"unknown curve_mode {self.curve_mode!r}; available: {CURVE_MODES}")
        if self.curve_drift_deg < 0 or not 0.0 <= self.curve_reversion <= 1.0 or self.curve_tortuosity < 1.0:
            raise ValueError("need curve_drift_deg >= 0, curve_reversion in [0, 1], curve_tortuosity >= 1")
        if len(self.oneway_pattern) != 2 or any(o not in ONEWAY_PATTERNS for o in self.oneway_pattern):
            raise ValueError(f"oneway_pattern needs two of {ONEWAY_PATTERNS}")
        if self.boundary not in BOUNDARY_MODES:
            raise ValueError(f"unknown boundary {self.boundary!r}; available: {BOUNDARY_MODES}")
        if self.ring_offset <= 0 or not 0.0 <= self.edge_drop_prob <= 1.0:
            raise ValueError("need ring_offset > 0 and edge_drop_prob in [0, 1]")
        if len(self.pattern_weights) != len(PATTERNS) or min(self.pattern_weights) < 0 \
                or sum(self.pattern_weights) <= 0:
            raise ValueError(f"pattern_weights needs {len(PATTERNS)} weights >= 0 ({', '.join(PATTERNS)}), "
                             "not all 0")
        if not all(0.0 <= v <= 1.0 for v in (self.organic, self.suburb_branch_prob, self.suburb_end_prob)):
            raise ValueError("organic, suburb_branch_prob and suburb_end_prob must be in [0, 1]")
        if self.radial_radius < 0 or self.radial_spokes < 2 or min(self.suburb_block_size) <= 0 \
                or self.style_blend_width < 0:
            raise ValueError("need radial_radius >= 0, radial_spokes >= 2, suburb_block_size > 0, "
                             "style_blend_width >= 0")
        if min(self.organic_scale, self.skew_scale) <= 0 or min(self.organic_max_deg, self.skew_deg,
                                                                  self.snap_deg) < 0:
            raise ValueError("need organic_scale, skew_scale > 0 and angles >= 0")
        if self.detour_ratio and self.detour_ratio < 1.0 or self.connect_radius <= 0:
            raise ValueError("need detour_ratio 0 (off) or >= 1, and connect_radius > 0")
        if not 0.0 <= self.culdesac_prob <= 1.0 or not 0.0 <= self.suburb_culdesac_prob <= 1.0:
            raise ValueError("culdesac_prob and suburb_culdesac_prob must be in [0, 1]")
        if self.culdesac_radius <= 0 or self.culdesac_speed <= 0:
            raise ValueError("culdesac_radius and culdesac_speed must be positive")
        if not 0.0 <= self.roundabout_prob <= 1.0 or min(self.roundabout_radius, self.roundabout_radius_avenue,
                                                          self.roundabout_speed) <= 0:
            raise ValueError("need roundabout_prob in [0, 1] and positive roundabout radii and speed")
        if not 0.0 <= self.stagger_prob <= 1.0 or self.stagger_offset <= 0 or not 3 <= self.max_degree <= 8:
            raise ValueError("need stagger_prob in [0, 1], stagger_offset > 0, max_degree in [3, 8]")
        if self.global_goal == "field" and self.curve_mode != "none":
            raise ValueError("curve_mode is for the grid goal; the field goal bends streets with "
                             "organic, contour and suburb patterns")

    @classmethod
    def preset(cls, name: str, **overrides: Any) -> "LSystemParams":
        if name not in PRESETS:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(**{**PRESETS[name], **overrides})


PRESETS: dict[str, dict[str, Any]] = {
    # Exactly a 10 x 10 grid: grown from a corner, branching everywhere.
    "grid": {
        "extent": (2250.0, 720.0),
        "start": (0.0, 0.0),
        "block_size": (250.0, 80.0),
        "branch_prob": 1.0,
    },
    # Midtown Manhattan: 10 avenues ~250 m apart and 30 streets ~80 m apart, rotated
    # ~29 deg from true north, both alternating one-way (avenues 4 lanes, streets 2)
    # at the 25 mph city limit, with a two-way ring road around the grid.
    "manhattan_like": {
        "extent": (9 * 250.0, 29 * 80.0),
        "start": (0.0, 0.0),
        "block_size": (250.0, 80.0),
        "branch_prob": 1.0,
        "rotation_deg": 29.0,
        "major_lanes": (4, 0),
        "minor_lanes": (2, 0),
        "major_speed": 11.2,
        "minor_speed": 11.2,
        "oneway_pattern": ("alternating", "alternating"),
        "boundary": "ring",
    },
    # Organic town: streets drift smoothly away from the grid and bow between junctions.
    "organic_curved": {
        "extent": (1500.0, 900.0),
        "start": (0.5, 0.5),
        "block_size": (200.0, 120.0),
        "branch_prob": 0.75,
        "length_jitter": 0.12,
        "snap_radius": 20.0,
        "min_segment_length": 50.0,
        "min_angle_deg": 50.0,
        "curve_mode": "drift",
        "curve_drift_deg": 8.0,
        "curve_reversion": 0.25,
        "curve_tortuosity": 1.03,
    },
    # Hill town for input maps: streets follow the terrain's contours (San Francisco rule).
    "hill_town": {
        "extent": (3000.0, 2400.0),
        "start": (0.5, 0.5),
        "block_size": (180.0, 100.0),
        "branch_prob": 0.85,
        "length_jitter": 0.1,
        "snap_radius": 20.0,
        "min_segment_length": 40.0,
        "min_angle_deg": 40.0,
        "curve_mode": "contour",
        "curve_drift_deg": 4.0,
        "curve_reversion": 0.5,
        "curve_tortuosity": 1.02,
    },
    # Same goal with noise: wobbly headings and lengths, some missing branches.
    "grid_organic": {
        "extent": (1500.0, 900.0),
        "start": (0.5, 0.5),
        "block_size": (200.0, 100.0),
        "branch_prob": 0.85,
        "angle_jitter_deg": 6.0,
        "length_jitter": 0.12,
        "snap_radius": 20.0,
        "min_segment_length": 50.0,
        "min_angle_deg": 60.0,
    },
    # Paris (field goal): a star of avenues and rings around the centre, a grid outside.
    "paris": {
        "global_goal": "field",
        "extent": (2000.0, 1600.0),
        "start": (0.5, 0.5),
        "block_size": (200.0, 120.0),
        "branch_prob": 0.9,
        "snap_radius": 20.0,
        "min_segment_length": 40.0,
        "min_angle_deg": 30.0,
        "pattern_weights": (0.25, 1.0, 0.0, 0.0, 0.0),
        "radial_radius": 600.0,
        "radial_spokes": 6,
        "max_degree": 7,
    },
    # Organic town (field goal): the grid bent by smooth noise, snapping only where nearly straight.
    "organic_town": {
        "global_goal": "field",
        "extent": (1500.0, 1200.0),
        "start": (0.5, 0.5),
        "block_size": (200.0, 110.0),
        "branch_prob": 0.85,
        "length_jitter": 0.1,
        "snap_radius": 20.0,
        "min_segment_length": 40.0,
        "min_angle_deg": 35.0,
        "organic": 0.8,
        "stagger_prob": 0.3,
        "roundabout_prob": 0.1,
    },
    # Skewed grid (field goal): streets cross the avenues at 60-90 deg, in patches.
    "skewed_grid": {
        "global_goal": "field",
        "extent": (1500.0, 1200.0),
        "start": (0.5, 0.5),
        "block_size": (200.0, 110.0),
        "branch_prob": 0.95,
        "snap_radius": 20.0,
        "min_segment_length": 40.0,
        "min_angle_deg": 30.0,
        "skew_deg": 30.0,
        "skew_scale": 2500.0,
    },
    # A city-sized area for use with input maps (pipeline --maps): highways
    # between population centres, noisy grid streets in between.
    "city": {
        "extent": (3000.0, 2400.0),
        "start": (0.5, 0.5),
        "block_size": (180.0, 100.0),
        "branch_prob": 0.9,
        "angle_jitter_deg": 3.0,
        "length_jitter": 0.1,
        "snap_radius": 20.0,
        "min_segment_length": 40.0,
        "min_angle_deg": 40.0,
    },
    # Suburb (field goal): curvy, sparsely branching streets, few loops, turn-around
    # bulbs at most dead ends (Teng & Bidarra's suburban patches).
    "suburb": {
        "global_goal": "field",
        "extent": (1500.0, 1200.0),
        "start": (0.5, 0.5),
        "snap_radius": 20.0,
        "min_segment_length": 30.0,
        "min_angle_deg": 50.0,
        "pattern_weights": (0.0, 0.0, 0.0, 0.0, 1.0),
        "suburb_culdesac_prob": 0.9,
        "minor_speed": 8.3,
        "roundabout_prob": 0.15,
    },
    # A mixed city for input maps (pipeline --maps, field goal): districts of grid, radial,
    # organic, contour and suburb streets from the style map, each grid at its own angle;
    # highways between population centres.
    "mixed_city": {
        "global_goal": "field",
        "extent": (3000.0, 2400.0),
        "start": (0.5, 0.5),
        "block_size": (180.0, 100.0),
        "branch_prob": 0.9,
        "length_jitter": 0.1,
        "snap_radius": 20.0,
        "min_segment_length": 40.0,
        "min_angle_deg": 35.0,
        "pattern_weights": (0.5, 0.0, 0.3, 0.0, 0.2),  # without a style map
        "stagger_prob": 0.15,
        "detour_ratio": 2.5,
        "max_degree": 7,
        "roundabout_prob": 0.08,
    },
}
LSystemParams.PRESETS = PRESETS  # type: ignore[attr-defined]


def grid_extent(n_x: int, n_y: int, block_size: tuple[float, float]) -> tuple[float, float]:
    """Extent of a grid with n_x roads along local y (avenues) and n_y along local x
    (streets), grown from start (0, 0) with branch_prob 1."""
    if n_x < 1 or n_y < 1 or n_x * n_y < 2:
        raise ValueError("need at least two grid nodes")
    return (n_x - 1) * block_size[0], (n_y - 1) * block_size[1]


@dataclass(order=True)
class _Proposal:
    t: int
    seq: int  # FIFO tie-break keeps the order deterministic
    node: int  # start node
    axis: int  # STREET_X / AVENUE_Y: grid road along local x / y; HIGHWAY
    sign: int  # +1 / -1 along the axis (grid roads)
    heading: float = 0.0  # highways, field goal: current direction [rad, local frame]
    line: int = 0  # grid roads: index of the grid line (one-way patterns)
    follow: bool = False  # field goal: continues a road (turn limited), not a new branch


class _SpatialHash:
    """Uniform grid of cells -> ids, for points and segments."""

    def __init__(self, cell: float) -> None:
        self.cell = cell
        self.cells: dict[tuple[int, int], set[int]] = defaultdict(set)
        self.where: dict[int, list[tuple[int, int]]] = {}

    def _span(self, x0: float, y0: float, x1: float, y1: float, pad: float = 0.0):
        c = self.cell
        for i in range(math.floor((min(x0, x1) - pad) / c), math.floor((max(x0, x1) + pad) / c) + 1):
            for j in range(math.floor((min(y0, y1) - pad) / c), math.floor((max(y0, y1) + pad) / c) + 1):
                yield i, j

    def add(self, key: int, a: Point, b: Optional[Point] = None) -> None:
        b = b or a
        cells = list(self._span(a[0], a[1], b[0], b[1]))
        for c in cells:
            self.cells[c].add(key)
        self.where[key] = cells

    def remove(self, key: int) -> None:
        for c in self.where.pop(key, []):
            self.cells[c].discard(key)

    def query(self, a: Point, b: Point, pad: float) -> set[int]:
        found: set[int] = set()
        for c in self._span(a[0], a[1], b[0], b[1], pad):
            found |= self.cells.get(c, set())
        return found


def _segment_hit(p: Point, q: Point, a: Point, b: Point) -> Optional[tuple[float, float]]:
    """(u along p->q, v along a->b) of the crossing point, or None (incl. parallel)."""
    rx, ry = q[0] - p[0], q[1] - p[1]
    sx, sy = b[0] - a[0], b[1] - a[1]
    den = rx * sy - ry * sx
    if abs(den) < EPS:
        return None
    qpx, qpy = a[0] - p[0], a[1] - p[1]
    u = (qpx * sy - qpy * sx) / den
    v = (qpx * ry - qpy * rx) / den
    if -EPS <= u <= 1 + EPS and -EPS <= v <= 1 + EPS:
        return u, v
    return None


def _project(pt: Point, a: Point, b: Point) -> tuple[float, float]:
    """(v along a->b clamped to [0, 1], distance from pt to that point)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    v = 0.0 if length2 < EPS else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / length2))
    px, py = a[0] + v * dx, a[1] + v * dy
    return v, math.hypot(pt[0] - px, pt[1] - py)


def _angle_between(u: Point, v: Point) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) /
                                                    (math.hypot(*u) * math.hypot(*v))))))


def _max_turn(p: LSystemParams, axis: int) -> float:
    """Largest heading change [rad] at a node of a curved street, so that a bend
    between two segments of the block length can still get its design radius."""
    v = p.minor_speed if axis == STREET_X else p.major_speed
    params = DesignParams()
    e = params.e_max_street if params.banking else 0.0
    radius = design.min_radius(v, e, params)
    spiral = design.spiral_length(v, radius, e, max(p.minor_lanes) * p.lane_width, params)
    room = max(p.block_size[axis] / 2 - spiral / 2, 1.0)
    return 2 * math.atan(room / radius)


class _Growth:
    """Mutable network state while growing (local frame)."""

    def __init__(self, p: LSystemParams, rng: np.random.Generator,
                 maps: Optional[MapSet] = None, seed: int = 0) -> None:
        self.p, self.rng = p, rng
        self.pos: dict[int, Point] = {}
        self.edges: dict[int, tuple[int, int, int]] = {}  # id -> (a, b, axis)
        self.line_of: dict[int, int] = {}  # edge id -> grid line index
        self.adj: dict[int, set[int]] = defaultdict(set)  # node -> edge ids
        cell = max(p.block_size)
        self.node_index, self.edge_index = _SpatialHash(cell), _SpatialHash(cell)
        self.next_node = self.next_edge = 1
        self.queue: list[_Proposal] = []
        self.seq = 0
        self.maps = _MapView(maps, p)
        self.bridges: set[int] = set()
        self.highway_phase = False
        self.deferred: list[tuple[int, int, int, int, float, int]] = []  # streets spawned by highways
        self.start_pt: Point = (p.start[0] * p.extent[0], p.start[1] * p.extent[1])
        self.max_turn = {axis: _max_turn(p, axis) for axis in (STREET_X, AVENUE_Y)}
        self.seed = seed
        self.field = self.make_field(seed) if p.global_goal == "field" else None

    def make_field(self, seed: int) -> StreetField:
        p = self.p
        fp = FieldParams(
            grid_angle=math.pi / 2 + math.radians(p.grid_angle_deg),  # avenues along local y
            block=p.block_size, suburb_block=p.suburb_block_size,
            branch_prob=p.branch_prob, suburb_branch_prob=p.suburb_branch_prob,
            radial_centers=self.maps.radial_centers() or ((p.radial_center[0] * p.extent[0],
                                                           p.radial_center[1] * p.extent[1]),),
            radial_radius=p.radial_radius, organic=p.organic,
            organic_max=math.radians(p.organic_max_deg), organic_scale=p.organic_scale,
            skew=math.radians(p.skew_deg), skew_scale=p.skew_scale, snap=math.radians(p.snap_deg),
            weights=tuple(p.pattern_weights),
        )
        return StreetField(fp, np.random.default_rng([seed, SALT_FIELD]), self.maps.style_weights(),
                           self.maps.gradient if self.maps.elevation is not None else None,
                           self.maps.district_angle())

    # --- mutation -----------------------------------------------------------------------

    def add_node(self, pt: Point) -> int:
        nid = self.next_node
        self.next_node += 1
        self.pos[nid] = pt
        self.node_index.add(nid, pt)
        return nid

    def add_edge(self, a: int, b: int, axis: int, line: int = 0) -> int:
        eid = self.next_edge
        self.next_edge += 1
        self.edges[eid] = (a, b, axis)
        self.line_of[eid] = line
        self.adj[a].add(eid)
        self.adj[b].add(eid)
        self.edge_index.add(eid, self.pos[a], self.pos[b])
        return eid

    def split_edge(self, eid: int, pt: Point) -> int:
        a, b, axis = self.edges.pop(eid)
        line = self.line_of.pop(eid)
        self.adj[a].discard(eid)
        self.adj[b].discard(eid)
        self.edge_index.remove(eid)
        mid = self.add_node(pt)
        first, second = self.add_edge(a, mid, axis, line), self.add_edge(mid, b, axis, line)
        if eid in self.bridges:  # only the pieces still over water stay bridges
            self.bridges.discard(eid)
            self.bridges |= {piece for piece, (u, v) in ((first, (a, mid)), (second, (mid, b)))
                             if self.maps.crosses_water(self.pos[u], self.pos[v])}
        return mid

    def propose(self, t: int, node: int, axis: int, sign: int, heading: float = 0.0,
                line: Optional[int] = None, follow: bool = False) -> None:
        """Queue a proposal; a new grid line (line None) gets its index from the node."""
        if line is None:
            line = self.line_index(node, axis)
        if self.highway_phase and axis != HIGHWAY:
            self.deferred.append((t, node, axis, sign, heading, line))  # streets wait for phase 2
            return
        self.seq += 1
        heapq.heappush(self.queue, _Proposal(t, self.seq, node, axis, sign, heading, line, follow))

    def line_index(self, node: int, axis: int) -> int:
        """Index of the grid line along axis through node, counted from the seed point's."""
        if axis not in (STREET_X, AVENUE_Y):
            return 0
        across = 1 - axis  # avenues (along y) are spaced along x and vice versa
        return round((self.pos[node][across] - self.start_pt[across]) / self.p.block_size[across])

    # --- local constraints --------------------------------------------------------------

    def street_end(self, prop: _Proposal) -> Optional[tuple[Point, bool]]:
        """Grid goal end point after bounds and map constraints, and whether it may go straight on."""
        p, rng = self.p, self.rng
        a = self.pos[prop.node]
        if self.field is not None:
            heading, length = self.field_step(prop, a)
        else:
            base = 0.0 if prop.axis == STREET_X else math.pi / 2
            heading = base + (0.0 if prop.sign > 0 else math.pi)
            if p.curve_mode != "none":
                prop.heading = self.curve_deviation(prop, a, heading)
                heading += prop.heading
            length = p.block_size[prop.axis]
        if p.angle_jitter_deg:
            heading += math.radians(rng.normal(0.0, p.angle_jitter_deg))
        if p.length_jitter:
            length *= max(0.3, 1.0 + rng.normal(0.0, p.length_jitter))
        end = (a[0] + length * math.cos(heading), a[1] + length * math.sin(heading))
        if not self.inside(end):
            return None  # 1. bounds
        if not self.maps.active:
            return end, True
        return self.maps.legal_street(a, end)

    def field_step(self, prop: _Proposal, a: Point) -> tuple[float, float]:
        """Field goal: heading and length of the next segment from a.

        The heading is the field direction at the segment's midpoint (a
        midpoint streamline step, so rings stay round), closest to the
        proposal's heading. A continuation (or a spoke of a radial star, where
        the field has no direction at the centre) predicts the midpoint along
        its own heading and turns at most max_turn; a new branch along the
        field direction at a.
        """
        role = 1 - prop.axis  # AVENUE_Y -> MAJOR, STREET_X -> MINOR
        length = self.field.length(a, role)
        first = prop.heading if prop.follow else self.field.direction(a, role, prop.heading)
        mid = (a[0] + 0.5 * length * math.cos(first), a[1] + 0.5 * length * math.sin(first))
        heading = self.field.direction(mid, role, first)
        if prop.follow:
            limit = self.max_turn[prop.axis]
            heading = prop.heading + max(-limit, min(limit, _wrap(heading - prop.heading)))
        prop.heading = heading
        return heading, length

    def curve_deviation(self, prop: _Proposal, a: Point, axis_heading: float) -> float:
        """Curved streets: the next segment's deviation [rad] from its grid axis.

        An Ornstein-Uhlenbeck step on the deviation: pulled back by
        curve_reversion towards a target (0, or for "contour" the terrain's
        contour direction weighted by its slope), plus curve_drift_deg noise,
        the change limited so that the bend can still get its design radius.
        """
        p = self.p
        target = 0.0
        if p.curve_mode == "contour":
            target = self.maps.contour_deviation(a, axis_heading)
        new = prop.heading + p.curve_reversion * (target - prop.heading)
        new += math.radians(self.rng.normal(0.0, p.curve_drift_deg))
        limit = self.max_turn[prop.axis]
        return prop.heading + max(-limit, min(limit, new - prop.heading))

    def bow_streets(self) -> None:
        """Curved streets: give every street between two nodes a bow node, so that its
        length / chord reaches curve_tortuosity, where the bow stays legal."""
        p = self.p
        rise = math.sqrt(p.curve_tortuosity ** 2 - 1.0)
        for eid in sorted(self.edges):
            a, b, axis = self.edges[eid]
            if axis == HIGHWAY or eid in self.bridges:
                continue
            pa, pb = self.pos[a], self.pos[b]
            length = math.dist(pa, pb)
            if length < 2 * p.min_segment_length:
                continue
            side = 1.0 if self.rng.random() < 0.5 else -1.0
            h = side * rise * length / 2
            nx_, ny_ = -(pb[1] - pa[1]) / length, (pb[0] - pa[0]) / length
            mid = ((pa[0] + pb[0]) / 2 + h * nx_, (pa[1] + pb[1]) / 2 + h * ny_)
            if self.bow_ok(eid, pa, pb, mid):
                self.split_edge(eid, mid)

    def bow_ok(self, eid: int, pa: Point, pb: Point, mid: Point) -> bool:
        p = self.p
        if not self.inside(mid):
            return False
        for n in self.node_index.query(mid, mid, p.snap_radius):
            if math.dist(self.pos[n], mid) < p.snap_radius:
                return False
        for q0, q1 in ((pa, mid), (mid, pb)):
            for other in self.edge_index.query(q0, q1, p.snap_radius):
                if other == eid:
                    continue
                oa, ob, _ = self.edges[other]
                hit = _segment_hit(q0, q1, self.pos[oa], self.pos[ob])
                if hit is not None and EPS < hit[0] < 1 - EPS:
                    return False
            if self.maps.active:
                if self.maps.terrain is not None:
                    x, y, _ = self.maps._line(q0, q1)
                    if self.maps._illegal_for_streets(x, y)[1:-1].any():
                        return False
                if self.maps._too_steep(q0, q1):
                    return False
        return True

    def ring_road(self) -> None:
        """boundary "ring": every grid road ending near the edge of the area (within the
        largest block size) continues straight out to a two-way ring road ring_offset
        outside the area, like the roads of a Manhattan grid meeting a ring.

        Extensions keep their road's axis and grid line (one-way direction). One
        that would cross another road, or end closer than min_segment_length to a
        ring corner or to the previous extension on its side, is left out.
        """
        p = self.p
        w, h = p.extent
        lo, hi = (-p.ring_offset, -p.ring_offset), (w + p.ring_offset, h + p.ring_offset)
        reach = max(p.block_size)
        cos45 = math.sqrt(0.5)
        # Sides as (coordinate across it, outward sign); stubs per side: (along, node, end, axis, line)
        sides = ((1, -1), (0, 1), (1, 1), (0, -1))  # bottom, right, top, left: counter-clockwise
        stubs: dict[int, list[tuple[float, int, Point, int, int]]] = {k: [] for k in range(4)}
        for node in sorted(self.pos):
            pt = self.pos[node]
            for k, (c, sign) in enumerate(sides):
                if abs(pt[c] - (p.extent[c] if sign > 0 else 0.0)) > reach:
                    continue
                inward, outward = None, False
                for eid in sorted(self.adj[node]):
                    v = self._vec(node, self._other(eid, node))
                    d = sign * v[c] / math.hypot(*v)
                    outward |= d > cos45
                    if d < -cos45 and self.edges[eid][2] in (STREET_X, AVENUE_Y):
                        inward = eid
                if inward is None or outward:
                    continue
                v = self._vec(self._other(inward, node), node)  # along the road, outwards
                t = ((hi if sign > 0 else lo)[c] - pt[c]) / v[c]
                end = (pt[0] + t * v[0], pt[1] + t * v[1])
                along = end[1 - c]
                if not lo[1 - c] + p.min_segment_length <= along <= hi[1 - c] - p.min_segment_length:
                    continue
                if self.crosses_road(pt, end, {node}):
                    continue
                stubs[k].append((along, node, end, *self.edges[inward][2:], self.line_of[inward]))
        corners = [(lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1])]
        ring = []
        for k, (c, sign) in enumerate(sides):
            ring.append(self.add_node(corners[k]))
            counter_clockwise = sign > 0 if c == 0 else sign < 0  # bottom, right: ascending
            last = None
            for along, node, end, axis, line in sorted(stubs[k], reverse=not counter_clockwise):
                if last is not None and abs(along - last) < p.min_segment_length:
                    continue
                last = along
                stop = self.add_node(end)
                self.add_edge(node, stop, axis, line)
                ring.append(stop)
        for a, b in zip(ring, ring[1:] + ring[:1]):
            self.add_edge(a, b, RING)

    def connect_pass(self) -> None:
        """detour_ratio: join a node to a nearby one when the network trip between
        them is more than detour_ratio times their straight distance (Lechner's
        connector agents). Dead ends go first, then the other nodes in random
        order; each node gets at most one connector, to the nearest such node
        whose segment passes the local constraints."""
        import networkx as nx

        p, rng = self.p, np.random.default_rng([self.seed, SALT_CONNECT])
        g = nx.Graph()
        for a, b, _ in self.edges.values():
            g.add_edge(a, b, weight=math.dist(self.pos[a], self.pos[b]))
        nodes = sorted(n for n in self.pos if self.adj[n])
        dead = [n for n in nodes if len(self.adj[n]) == 1]
        rest = [n for n in nodes if len(self.adj[n]) > 1]
        order = dead + [rest[k] for k in rng.permutation(len(rest))]
        added = 0
        for a in order:
            pa = self.pos[a]
            near = sorted((math.dist(pa, self.pos[n]), n) for n in self.node_index.query(pa, pa, p.connect_radius)
                          if n != a and self.adj[n])
            near = [(d, n) for d, n in near if p.min_segment_length <= d <= p.connect_radius]
            if not near:
                continue
            reach = nx.single_source_dijkstra_path_length(g, a, cutoff=p.detour_ratio * near[-1][0])
            for d, b in near[:CONNECT_CANDIDATES]:
                if reach.get(b, math.inf) <= p.detour_ratio * d:
                    continue  # already close over the network
                if not self.acceptable(a, self.pos[b], b, None):
                    continue
                if self.maps.active:
                    legal = self.maps.legal_street(pa, self.pos[b])
                    if legal is None or legal[0] != self.pos[b] or self.maps.crosses_water(pa, self.pos[b]):
                        continue
                self.add_edge(a, b, STREET_X)
                g.add_edge(a, b, weight=d)
                added += 1
                break
        log.debug("lsystem: %d connectors (detour ratio %.2f)", added, p.detour_ratio)

    def culdesac_prob(self, node: Node) -> float:
        """Chance of a bulb at a dead end (world coordinates): culdesac_prob, or more
        in suburbs (field goal)."""
        p = self.p
        if self.field is None or not p.suburb_culdesac_prob:
            return p.culdesac_prob
        suburb = self.field.weights(self.maps.to_local(node.x, node.y))[SUBURB]
        return max(p.culdesac_prob, suburb * p.suburb_culdesac_prob)

    def culdesac_allowed(self, footprint: Any) -> bool:
        """A bulb or a roundabout (world polygon) must lie inside the area and on legal ground."""
        pts = [self.maps.to_local(x, y) for x, y in footprint.exterior.coords]
        if not all(self.inside(pt) for pt in pts):
            return False
        if self.maps.terrain is not None:
            x, y = np.array([pt[0] for pt in pts]), np.array([pt[1] for pt in pts])
            if self.maps._illegal_for_streets(x, y).any():
                return False
        return True

    def crosses_road(self, a: Point, b: Point, touching: set[int]) -> bool:
        """Whether segment a -> b crosses a road that touches none of the touching nodes."""
        for eid in self.edge_index.query(a, b, 0.0):
            ea, eb, _ = self.edges[eid]
            if touching & {ea, eb}:
                continue
            hit = _segment_hit(a, b, self.pos[ea], self.pos[eb])
            if hit and EPS < hit[0] < 1 - EPS:
                return True
        return False

    def inside(self, pt: Point) -> bool:
        w, h = self.p.extent
        return -EPS <= pt[0] <= w + EPS and -EPS <= pt[1] <= h + EPS

    def resolve(self, node: int, end: Point) -> tuple[str, Any]:
        """Where a segment from node towards end ends: ("free", point), ("node", id)
        or ("split", (edge, point))."""
        p = self.p
        a = self.pos[node]
        # 2. intersect: first crossing with a road not touching the start node.
        best = None
        for eid in self.edge_index.query(a, end, p.snap_radius):
            ea, eb, _ = self.edges[eid]
            if node in (ea, eb):
                continue
            hit = _segment_hit(a, end, self.pos[ea], self.pos[eb])
            if hit and hit[0] > EPS and (best is None or hit[0] < best[0]):
                best = (hit[0], hit[1], eid)
        if best:
            u, v, eid = best
            ea, eb, _ = self.edges[eid]
            pt = (a[0] + u * (end[0] - a[0]), a[1] + u * (end[1] - a[1]))
            for n in (ea, eb):  # crossing right next to a node: use the node
                if math.dist(pt, self.pos[n]) <= p.snap_radius:
                    return "node", n
            return "split", (eid, pt)

        # 3. snap to a node, then to a road.
        near = [n for n in self.node_index.query(end, end, p.snap_radius) if n != node]
        near = [(math.dist(end, self.pos[n]), n) for n in near]
        near = [(d, n) for d, n in near if d <= p.snap_radius]
        if near:
            return "node", min(near)[1]
        best_edge = None
        for eid in self.edge_index.query(end, end, p.snap_radius):
            ea, eb, _ = self.edges[eid]
            if node in (ea, eb):
                continue
            v, d = _project(end, self.pos[ea], self.pos[eb])
            if d <= p.snap_radius and (best_edge is None or d < best_edge[0]):
                best_edge = (d, v, eid)
        if best_edge:
            _, v, eid = best_edge
            ea, eb, _ = self.edges[eid]
            pa, pb = self.pos[ea], self.pos[eb]
            return "split", (eid, (pa[0] + v * (pb[0] - pa[0]), pa[1] + v * (pb[1] - pa[1])))
        return "free", end

    def acceptable(self, a: int, end: Point, end_node: Optional[int], split_edge: Optional[int]) -> bool:
        """4. length, duplicates, angles, and no crossing of other roads."""
        p, pa = self.p, self.pos[a]
        if math.dist(pa, end) < p.min_segment_length:
            return False
        if end_node is not None:
            if end_node == a or any(end_node in self.edges[e][:2] for e in self.adj[a]):
                return False
        if split_edge is not None:  # pieces of a split road must stay long enough too
            ea, eb, _ = self.edges[split_edge]
            if min(math.dist(end, self.pos[ea]), math.dist(end, self.pos[eb])) < p.min_segment_length:
                return False
        direction = (end[0] - pa[0], end[1] - pa[1])
        for eid in self.adj[a]:
            other = self._other(eid, a)
            if _angle_between(direction, self._vec(a, other)) < p.min_angle_deg:
                return False
        back = (-direction[0], -direction[1])
        if end_node is not None:
            for eid in self.adj[end_node]:
                if _angle_between(back, self._vec(end_node, self._other(eid, end_node))) < p.min_angle_deg:
                    return False
        if split_edge is not None:  # the split road continues both ways from the new node
            ea, eb, _ = self.edges[split_edge]
            for n in (ea, eb):
                if _angle_between(back, (self.pos[n][0] - end[0], self.pos[n][1] - end[1])) < p.min_angle_deg:
                    return False
        # The (possibly snapped) segment must not cross any other road.
        touching = {a} | ({end_node} if end_node is not None else set())
        for eid in self.edge_index.query(pa, end, 0.0):
            if eid == split_edge:
                continue
            ea, eb, _ = self.edges[eid]
            if touching & {ea, eb}:
                continue
            hit = _segment_hit(pa, end, self.pos[ea], self.pos[eb])
            if hit and EPS < hit[0] < 1 - EPS:
                return False
        return True

    def _other(self, eid: int, n: int) -> int:
        a, b, _ = self.edges[eid]
        return b if a == n else a

    def _vec(self, a: int, b: int) -> Point:
        return self.pos[b][0] - self.pos[a][0], self.pos[b][1] - self.pos[a][1]

    # --- main loop ------------------------------------------------------------------------

    def run(self) -> None:
        start_pt = self.start_pt
        if self.maps.active:
            start_pt = self.maps.legal_start(start_pt)
        self.start_pt = start_pt
        start = self.add_node(start_pt)
        if self.maps.highways:
            self.highway_phase = True
            for heading in self.maps.seed_headings(start_pt):
                self.propose(0, start, HIGHWAY, 1, heading)
            self.loop()
            self.highway_phase = False
            log.debug("lsystem: %d highway segments, %d bridges", len(self.edges), len(self.bridges))
        if self.field is not None:
            self.seed_field(start)
        else:
            for axis in (AVENUE_Y, STREET_X):
                for sign in (1, -1):
                    self.propose(0, start, axis, sign)
        for t, node, axis, sign, heading, line in self.deferred:
            self.propose(t, node, axis, sign, heading, line=line)
        self.loop()
        if self.p.detour_ratio:
            self.connect_pass()
        if (self.p.curve_mode != "none" or self.field is not None) and self.p.curve_tortuosity > 1.0:
            self.bow_streets()
        if self.p.boundary == "ring":
            self.ring_road()

    def seed_field(self, start: int) -> None:
        """Field goal: avenues and streets both ways from the seed point, or a star of
        radial_spokes avenues where the seed point is a radial centre. Every other
        radial centre in the area (radial weight >= 0.5 there) gets a star too
        (Paris's étoiles), which grows until it meets the rest of the network."""
        p, pt = self.p, self.pos[start]
        stars = [c for c in self.field.params.radial_centers
                 if self.inside(c) and self.field.weights(c)[RADIAL] >= 0.5]
        at_start = [c for c in stars if math.dist(pt, c) <= p.snap_radius]
        if at_start:
            self.star(start)
        else:
            major = self.field.major_angle(pt) or math.pi / 2
            for axis, base in ((AVENUE_Y, major), (STREET_X, major + math.pi / 2)):
                for sign in (1, -1):
                    self.propose(0, start, axis, sign, base if sign > 0 else base + math.pi)
        for c in stars:
            if math.dist(pt, c) > max(p.block_size) and self.maps.legal_point(c):
                self.star(self.add_node(c))

    def star(self, node: int) -> None:
        """radial_spokes avenues leaving node evenly (their headings followed, as the
        field has no direction at a radial centre)."""
        n = self.p.radial_spokes
        for k in range(n):
            self.propose(0, node, AVENUE_Y, 1, 2 * math.pi * k / n, follow=True)

    def loop(self) -> None:
        while self.queue and len(self.edges) < self.p.max_segments:
            prop = heapq.heappop(self.queue)
            if prop.axis == HIGHWAY:
                self.step_highway(prop)
            else:
                self.step_street(prop)

    def step_street(self, prop: _Proposal) -> None:
        proposed = self.street_end(prop)
        if proposed is None:
            return
        end, straight = proposed
        added = self.connect(prop.node, end, prop.axis, prop.line)
        if added and added[1] is not None:
            self.grow(prop, added[1], straight)

    def step_highway(self, prop: _Proposal) -> None:
        """Best direction by population whose segment passes the constraints."""
        for heading, end, bridge in self.maps.highway_candidates(self.pos[prop.node], prop.heading,
                                                                 self.inside):
            added = self.connect(prop.node, end, HIGHWAY)
            if added is None:
                continue  # rejected by the local constraints: try the next direction
            eid, node = added
            if bridge:
                self.bridges.add(eid)
            if node is not None:
                self.grow_highway(prop, node, heading)
            return

    def connect(self, a: int, end: Point, axis: int, line: int = 0) -> Optional[tuple[int, Optional[int]]]:
        """Resolve, check and add a segment from a towards end.

        Returns None if rejected, else (new edge id, free end node to grow
        from, or None when the segment ended on an existing road).
        """
        kind, data = self.resolve(a, end)
        if kind == "free":
            end, end_node, split = data, None, None
        elif kind == "node":
            end, end_node, split = self.pos[data], data, None
        else:
            split, end = data
            end_node = None
        if not self.acceptable(a, end, end_node, split):
            return None
        # legal_street checked the proposed segment; snapping may move it over water.
        if axis != HIGHWAY and kind != "free" and self.maps.crosses_water(self.pos[a], end, ends=False):
            return None
        if kind == "split":
            end_node = self.split_edge(split, end)
        elif kind == "free":
            end_node = self.add_node(end)
        eid = self.add_edge(a, end_node, axis, line)
        return eid, (end_node if kind == "free" else None)

    def grow(self, prop: _Proposal, node: int, straight: bool = True) -> None:
        """Global goal "grid": continue straight, branch perpendicular."""
        # Curved streets carry their deviation from the grid axis on: a straight
        # continuation keeps it, and branches leave perpendicular to the street.
        if self.field is not None:
            self.grow_field(prop, node, straight)
            return
        if straight:
            self.propose(prop.t + 1, node, prop.axis, prop.sign, prop.heading, prop.line)
        other = 1 - prop.axis
        for sign in (1, -1):
            if self.rng.random() < self.p.branch_prob:
                self.propose(prop.t + self.p.branch_delay, node, other, sign, prop.heading)

    def grow_field(self, prop: _Proposal, node: int, straight: bool = True) -> None:
        """Field goal: continue along the road (in suburbs it may end here instead,
        suburb_end_prob), branch to both sides along the other direction."""
        weights = self.field.weights(self.pos[node])
        ends = self.rng.random() < weights[SUBURB] * self.p.suburb_end_prob
        if straight and not ends:
            self.propose(prop.t + 1, node, prop.axis, prop.sign, prop.heading, prop.line, follow=True)
        branch_prob = self.field.branch_prob(self.pos[node])
        for sign in (1, -1):
            if self.rng.random() < branch_prob:
                self.propose(prop.t + self.p.branch_delay, node, 1 - prop.axis, sign,
                             prop.heading + sign * math.pi / 2)

    def grow_highway(self, prop: _Proposal, node: int, heading: float) -> None:
        """Continue, maybe branch a new highway, and spawn grid streets on both sides."""
        p = self.p
        self.propose(prop.t + 1, node, HIGHWAY, 1, heading)
        for side in (1, -1):
            normal = heading + side * math.pi / 2
            if self.rng.random() < p.highway_branch_prob:
                self.propose(prop.t + p.highway_branch_delay, node, HIGHWAY, 1, normal)
            if self.rng.random() < p.branch_prob:
                if self.field is not None:  # the field direction closest to the normal
                    axis = AVENUE_Y if self.field.closest_role(self.pos[node], normal) == 0 else STREET_X
                    self.propose(prop.t + p.branch_delay, node, axis, 1, normal)
                    continue
                # The grid direction closest to the highway's normal.
                c, s = math.cos(normal), math.sin(normal)
                axis = STREET_X if abs(c) >= abs(s) else AVENUE_Y
                sign = 1 if (c if axis == STREET_X else s) > 0 else -1
                self.propose(prop.t + p.branch_delay, node, axis, sign)


class _MapView:
    """The input maps as seen from the growth's local frame."""

    def __init__(self, maps: Optional[MapSet], p: LSystemParams) -> None:
        maps = maps or MapSet()
        self.p = p
        self.terrain, self.elevation, self.population = maps.terrain, maps.elevation, maps.population
        self.style = maps.style
        layers = list(maps.layers().values())
        self.active = bool(layers)
        self.highways = p.highways and self.population is not None
        # Sample along roads at half the finest cell size.
        self.step = min((m.grid.cell_size for m in layers), default=1.0) / 2
        theta = math.radians(p.rotation_deg)
        self.cos, self.sin = math.cos(theta), math.sin(theta)

    def sample(self, layer: MapLayer, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        ox, oy = self.p.origin
        return layer.sample(ox + self.cos * x - self.sin * y, oy + self.sin * x + self.cos * y)

    def _line(self, a: Point, b: Point) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Points from a to b about step apart (both ends included), and their distance from a."""
        length = math.dist(a, b)
        n = max(2, math.ceil(length / self.step) + 1)
        s = np.linspace(0.0, length, n)
        u = s / length if length > 0 else s
        return a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1]), s

    def _too_steep(self, a: Point, b: Point, on_land: bool = False) -> bool:
        """Grade over any GRADE_BASELINE stretch of a -> b above max_grade.

        on_land: skip stretches touching water (bridges are level).
        """
        if self.elevation is None or not self.p.max_grade:
            return False
        length = math.dist(a, b)
        n = max(2, math.ceil(length / GRADE_BASELINE) + 1)
        u = np.linspace(0.0, 1.0, n)
        x, y = a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1])
        grade = np.abs(np.diff(self.sample(self.elevation, x, y))) / (length / (n - 1))
        if on_land and self.terrain is not None:
            land = self.sample(self.terrain, x, y) != WATER
            grade = grade[land[1:] & land[:-1]]
        return bool(grade.size) and float(grade.max()) > self.p.max_grade

    def contour_deviation(self, a: Point, axis_heading: float) -> float:
        """Deviation [rad] from axis_heading towards the terrain contour at a, weighted
        by the slope (0 on flat ground, the full contour from CONTOUR_SLOPE on)."""
        if self.elevation is None:
            return 0.0
        gx, gy = self.gradient(a)
        slope = math.hypot(gx, gy)
        if slope < 1e-9:
            return 0.0
        contour = math.atan2(gx, -gy)  # perpendicular to the gradient
        dev = (contour - axis_heading + math.pi / 2) % math.pi - math.pi / 2  # either way along it
        dev = max(-math.radians(CONTOUR_MAX_DEG), min(math.radians(CONTOUR_MAX_DEG), dev))
        return dev * min(1.0, slope / CONTOUR_SLOPE)

    def gradient(self, a: Point) -> tuple[float, float]:
        """Terrain gradient (dz/dx, dz/dy) at a, by central differences (needs elevation)."""
        h = GRADIENT_STEP
        z = self.sample(self.elevation, np.array([a[0] + h, a[0] - h, a[0], a[0]]),
                        np.array([a[1], a[1], a[1] + h, a[1] - h]))
        return float((z[0] - z[1]) / (2 * h)), float((z[2] - z[3]) / (2 * h))

    def to_local(self, x: float, y: float) -> Point:
        ox, oy = self.p.origin
        dx, dy = x - ox, y - oy
        return self.cos * dx + self.sin * dy, -self.sin * dx + self.cos * dy

    def radial_centers(self) -> tuple[Point, ...]:
        """Radial pattern centres of a style map (local frame): its metadata's, or
        the centroid of its radial cells; empty without one or without radial cells."""
        if self.style is None:
            return ()
        centers = self.style.metadata.get("radial_centers")
        if centers is None:
            radial = self.style.data == STYLE_RADIAL
            if not radial.any():
                return ()
            x, y = self.style.grid.cell_centers()
            centers = [[float(x[radial].mean()), float(y[radial].mean())]]
        return tuple(self.to_local(cx, cy) for cx, cy in centers)

    def style_weights(self) -> Optional[Any]:
        """Per-pattern weights at a local point from a style map: each class's share
        of the cells within style_blend_width (box blur); None without a style map."""
        if self.style is None:
            return None
        from scipy.ndimage import uniform_filter

        layer, n = self.style, len(STYLE_CLASSES)
        size = max(1, round(self.p.style_blend_width / layer.grid.cell_size))
        stack = np.stack([uniform_filter((layer.data == k).astype(float), size, mode="nearest")
                          for k in range(n)], axis=-1)
        g = layer.grid

        def weights(pt: Point) -> np.ndarray:
            ox, oy = self.p.origin
            x, y = ox + self.cos * pt[0] - self.sin * pt[1], oy + self.sin * pt[0] + self.cos * pt[1]
            fc, fr = (x - g.origin[0]) / g.cell_size - 0.5, (y - g.origin[1]) / g.cell_size - 0.5
            return bilinear(stack, np.array(fc), np.array(fr))

        return weights

    def district_angle(self) -> Optional[Any]:
        """Grid angle [rad, local frame] at a local point: the nearest district site's
        (style map metadata), on top of grid_angle_deg; None without sites."""
        sites = self.style.metadata.get("sites") if self.style is not None else None
        if not sites:
            return None
        from scipy.spatial import cKDTree

        local = np.array([self.to_local(x, y) for x, y, _, _ in sites])
        base = math.pi / 2 + math.radians(self.p.grid_angle_deg - self.p.rotation_deg)
        angles = [base + math.radians(a) for _, _, _, a in sites]
        tree = cKDTree(local)
        return lambda pt: angles[int(tree.query(pt)[1])]

    def crosses_water(self, a: Point, b: Point, ends: bool = True) -> bool:
        """Whether a -> b touches water; ends=False ignores both end points
        (existing nodes, which may sit on a highway bridge)."""
        if self.terrain is None:
            return False
        x, y, _ = self._line(a, b)
        wet = self.sample(self.terrain, x, y) == WATER
        return bool(np.any(wet if ends else wet[1:-1]))

    def _illegal_for_streets(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        cls = self.sample(self.terrain, x, y)
        bad = cls == WATER
        if self.p.avoid_vegetation:
            bad |= cls == VEGETATION
        return bad

    def legal_point(self, pt: Point) -> bool:
        """Whether a road may start at pt (not in water / vegetation, populated enough)."""
        if self.terrain is not None and self._illegal_for_streets(np.array([pt[0]]), np.array([pt[1]]))[0]:
            return False
        if self.population is not None and self.p.street_min_density > 0:
            return bool(self.sample(self.population, np.array([pt[0]]), np.array([pt[1]]))[0]
                        >= self.p.street_min_density)
        return True

    def legal_start(self, pt: Point) -> Point:
        """The seed point, or the nearest legal cell centre inside the area if it is illegal."""
        if self.terrain is None or not self._illegal_for_streets(np.array([pt[0]]), np.array([pt[1]]))[0]:
            return pt
        wx, wy = self.terrain.grid.cell_centers()
        ox, oy = self.p.origin
        dx, dy = wx - ox, wy - oy  # world -> local: rotate back
        lx, ly = self.cos * dx + self.sin * dy, -self.sin * dx + self.cos * dy
        w, h = self.p.extent
        ok = (lx >= 0) & (lx <= w) & (ly >= 0) & (ly <= h) & ~self._illegal_for_streets(lx, ly)
        if not ok.any():
            log.warning("lsystem: no legal land in the area; starting on water")
            return pt
        d = np.where(ok, np.hypot(lx - pt[0], ly - pt[1]), np.inf)
        k = np.unravel_index(int(np.argmin(d)), d.shape)
        log.info("lsystem: seed point is in water / vegetation, moved %.0f m", float(d[k]))
        return float(lx[k]), float(ly[k])

    def legal_street(self, a: Point, end: Point) -> Optional[tuple[Point, bool]]:
        """Prune at water / vegetation, then check grade and population."""
        p = self.p
        straight = True
        if self.terrain is not None:
            x, y, s = self._line(a, end)
            bad = self._illegal_for_streets(x, y)
            bad[0] = False  # the start node exists already (it may sit on a highway)
            if bad.any():
                first = int(np.argmax(bad))
                keep = float(s[first - 1])
                length = float(s[-1])
                if keep < max(p.prune_factor * length, p.min_segment_length):
                    return None
                end = (a[0] + (end[0] - a[0]) * keep / length, a[1] + (end[1] - a[1]) * keep / length)
                straight = False  # ends at the shore: branch along it, don't continue
        if self._too_steep(a, end):
            return None
        if self.population is not None and p.street_min_density > 0:
            if self.sample(self.population, np.array([end[0]]), np.array([end[1]]))[0] < p.street_min_density:
                return None
        return end, straight

    # --- highways ---------------------------------------------------------------------------

    def ray_score(self, a: Point, heading: float) -> float:
        """Population along a ray, each sample weighted by 1 / distance (Parish & Mueller 3.2.1)."""
        p = self.p
        d = p.highway_ray_length * np.arange(1, p.highway_ray_samples + 1) / p.highway_ray_samples
        x, y = a[0] + d * math.cos(heading), a[1] + d * math.sin(heading)
        return float(np.sum(self.sample(self.population, x, y) / d))

    def seed_headings(self, a: Point) -> list[float]:
        """The most populated direction, and the best one at least 90 degrees from it."""
        headings = [2 * math.pi * k / SEED_RAYS for k in range(SEED_RAYS)]
        ranked = sorted(headings, key=lambda h: -self.ray_score(a, h))
        first = ranked[0]
        second = next(h for h in ranked if abs(_wrap(h - first)) >= math.pi / 2 - EPS)
        return [first, second]

    def highway_candidates(self, a: Point, heading: float,
                           inside: Any) -> Iterator[tuple[float, Point, bool]]:
        """(heading, end, bridge) for the fan of directions, best population first.

        Then, as a detour, the directions between highway_max_turn_deg and
        highway_max_detour_deg, smallest turn first: around a lake or along
        the contour of a hill.
        """
        p = self.p
        fan = math.radians(p.highway_max_turn_deg)
        turns = [0.0] if p.highway_rays == 1 else list(np.linspace(-fan, fan, p.highway_rays))
        ranked = sorted(turns, key=lambda t: (-self.ray_score(a, heading + t), abs(t)))
        step = fan / max(p.highway_rays // 2, 1) if fan > 0 else math.radians(10.0)
        detours = np.arange(fan + step, math.radians(p.highway_max_detour_deg) + EPS, step)
        ranked += [side * float(t) for t in detours for side in (1, -1)]
        for t in ranked:
            h = heading + float(t)
            segment = self.highway_segment(a, h, inside)
            if segment is not None:
                yield (h, *segment)

    def highway_segment(self, a: Point, heading: float, inside: Any) -> Optional[tuple[Point, bool]]:
        """End point and bridge flag, or None if the direction is not allowed.

        Water may be crossed once, up to max_bridge_length; the segment is
        stretched to the far shore when it would end in water.
        """
        p = self.p
        c, s_ = math.cos(heading), math.sin(heading)
        length = p.highway_length
        bridge = False
        if self.terrain is not None:
            reach = length + p.max_bridge_length
            s = np.arange(0.0, reach + self.step, self.step)
            water = self.sample(self.terrain, a[0] + s * c, a[1] + s * s_) == WATER
            within = s <= length + EPS
            if water[within].any():
                i = int(np.argmax(water))
                after = np.nonzero(~water[i:])[0]
                if not after.size:
                    return None
                j = i + int(after[0])
                if s[j] - s[i] > p.max_bridge_length:
                    return None
                length = max(length, float(s[j]))
                if water[j:][s[j:] <= length + EPS].any():
                    return None  # a second stretch of water
                bridge = True
        end = (a[0] + length * c, a[1] + length * s_)
        if not inside(end):
            return None
        if self._too_steep(a, end, on_land=True):
            return None
        if self.sample(self.population, np.array([end[0]]), np.array([end[1]]))[0] < p.highway_min_density:
            return None
        return end, bridge


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


@register("lsystem")
class LSystemGenerator(RoadNetworkGenerator[LSystemParams]):
    params_type = LSystemParams
    description = "Parish & Mueller style growth: priority queue, global goals, local constraints"
    uses_maps = True

    def generate(self, params: LSystemParams, seed: int, maps: Optional[MapSet] = None) -> RoadGraph:
        if maps is not None and maps.style is not None and params.global_goal != "field":
            # Only the field goal reads a style map; without it the grid grows as if it weren't there.
            maps = MapSet(**{k: v for k, v in maps.layers().items() if k != "style"})
            maps = maps if maps.layers() else None
        if maps is not None:
            bounds = self.map_bounds(params)
            for kind, layer in maps.layers().items():
                if not layer.grid.covers(bounds):
                    log.warning("lsystem: the %s map does not cover the whole area; "
                                "its edge values are used outside", kind)
        rng = np.random.default_rng(seed)
        growth = _Growth(params, rng, maps, seed)
        growth.run()
        graph = self._to_graph(growth, params, seed, maps)
        if params.edge_drop_prob and drop_edges(graph, params.edge_drop_prob,
                                                np.random.default_rng([seed, SALT_DROP])):
            assign_node_kinds(graph)
        if params.stagger_prob and stagger_crossings(graph, params.stagger_prob, params.stagger_offset,
                                                     np.random.default_rng([seed, SALT_STAGGER]),
                                                     params.min_angle_deg):
            assign_node_kinds(graph)
        if repair_short_edges(graph, max_degree=params.max_degree):
            assign_node_kinds(graph)
        if params.roundabout_prob and add_roundabouts(
                graph, np.random.default_rng([seed, SALT_ROUNDABOUT]), lambda node: params.roundabout_prob,
                params.roundabout_radius, params.roundabout_radius_avenue, params.roundabout_speed,
                growth.culdesac_allowed):
            assign_node_kinds(graph)
        if (params.culdesac_prob or (params.suburb_culdesac_prob and growth.field is not None)) \
                and add_culdesacs(graph, np.random.default_rng([seed, SALT_CULDESAC]), growth.culdesac_prob,
                                  params.culdesac_radius, params.culdesac_speed, growth.culdesac_allowed):
            assign_node_kinds(graph)
        if not graph.edges:
            log.warning("lsystem: no road could be grown from the seed point; on input maps it is "
                        "usually too steep around it (max_grade %.2f), or water, vegetation or "
                        "too few people (street_min_density) block every direction",
                        params.max_grade)
        return graph

    def map_kinds(self, params: LSystemParams) -> tuple[str, ...]:
        """The style map only matters to the field goal."""
        kinds = ("terrain", "elevation", "population")
        return (*kinds, "style") if params.global_goal == "field" else kinds

    def map_bounds(self, params: LSystemParams) -> Bounds:
        """World bounding box of the (rotated) growth area."""
        theta = math.radians(params.rotation_deg)
        c, s = math.cos(theta), math.sin(theta)
        w, h = params.extent
        ox, oy = params.origin
        m = params.ring_offset if params.boundary == "ring" else 0.0  # the ring lies outside
        box = ((-m, -m), (w + m, -m), (w + m, h + m), (-m, h + m))
        xs = [ox + c * x - s * y for x, y in box]
        ys = [oy + s * x + c * y for x, y in box]
        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def _to_graph(g: _Growth, p: LSystemParams, seed: int,
                  maps: Optional[MapSet] = None) -> RoadGraph:
        theta = math.radians(p.rotation_deg)
        c, s = math.cos(theta), math.sin(theta)
        graph = RoadGraph(metadata={
            "name": f"lsystem_{p.global_goal}_s{seed}",
            "generator": "lsystem",
            "seed": seed,
            "params": asdict(p),
        })
        # Renumber in creation order, dropping nodes that never got a road.
        used = sorted({n for a, b, _ in g.edges.values() for n in (a, b)})
        new_id = {old: k for k, old in enumerate(used, 1)}
        for old in used:
            x, y = g.pos[old]
            graph.add_node(Node(new_id[old], float(p.origin[0] + c * x - s * y),
                                float(p.origin[1] + s * x + c * y)))
        roads = {
            HIGHWAY: ("highway", p.highway_lanes, p.highway_speed),
            AVENUE_Y: ("avenue", p.major_lanes, p.major_speed),
            STREET_X: ("street", p.minor_lanes, p.minor_speed),
            RING: ("avenue", p.ring_lanes, p.major_speed),
        }
        pattern = {AVENUE_Y: p.oneway_pattern[0], STREET_X: p.oneway_pattern[1]}
        bridges = []
        for k, (old, (a, b, axis)) in enumerate(sorted(g.edges.items()), 1):
            road_class, lanes, speed = roads[axis]
            if pattern.get(axis) == "alternating":
                # Even grid lines run along +axis, odd ones against it, all lanes forward.
                along = g.pos[b][axis] - g.pos[a][axis]
                if (along > 0) != (g.line_of[old] % 2 == 0):
                    a, b = b, a
                lanes = (sum(lanes), 0)
            graph.add_edge(Edge(k, new_id[a], new_id[b], road_class, *lanes,
                                lane_width=p.lane_width, speed_limit=speed))
            if old in g.bridges:
                bridges.append(k)
        if maps is not None:
            graph.metadata["bridges"] = bridges  # edge ids of highways crossing water
        assign_node_kinds(graph)
        log.debug("lsystem: %d nodes, %d edges", len(graph.nodes), len(graph.edges))
        return graph
