"""HD-map settings of the OpenDRIVE builder (stage B).

One bundle the builder takes next to DesignParams and JunctionParams. With
enabled off, the builder writes the plain network: driving lanes, plain
marks, no signals or objects. It holds the cross-section with its turn
pockets, the road marks, junction control and traffic lights.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from roadgen.config import DEFAULTS, section
from roadgen.geometry.cross_section import CrossSectionParams, TurnLaneParams
from roadgen.opendrive.control import JunctionControlParams
from roadgen.opendrive.lights import TrafficLightParams
from roadgen.opendrive.marks import RoadMarkParams

_D = section(DEFAULTS, "hdmap")


@dataclass(frozen=True)
class HdMapParams:
    enabled: bool = _D["enabled"]  # off: the plain driving-lane network
    seed: int = DEFAULTS["seed"]  # random choices (stop share, light offsets, ...), salted per feature
    cross_section: CrossSectionParams = field(default_factory=CrossSectionParams)
    marks: RoadMarkParams = field(default_factory=RoadMarkParams)
    control: JunctionControlParams = field(default_factory=JunctionControlParams)
    lights: TrafficLightParams = field(default_factory=TrafficLightParams)
    turn_lanes: TurnLaneParams = field(default_factory=TurnLaneParams)

    @property
    def active_cross_section(self) -> Optional[CrossSectionParams]:
        """The cross-section the geometry uses (with the turn pockets), None with HD maps off."""
        return replace(self.cross_section, turn_lanes=self.turn_lanes) if self.enabled else None

    @property
    def active_marks(self) -> Optional[RoadMarkParams]:
        """The HD road marks, None with HD maps off (plain marks)."""
        return self.marks if self.enabled else None

    @classmethod
    def from_config(cls, cfg: dict, seed: Optional[int] = None, enabled: Optional[bool] = None) -> HdMapParams:
        """From a config's hdmap, cross_section, road_marks, junction_control, traffic_lights and turn_lanes sections; enabled (e.g. --hd / --no-hd)
        overrides hdmap.enabled when given."""
        on = section(cfg, "hdmap")["enabled"] if enabled is None else enabled
        return cls(enabled=on, seed=cfg["seed"] if seed is None else seed,
                   cross_section=CrossSectionParams(**section(cfg, "cross_section")),
                   marks=RoadMarkParams(**section(cfg, "road_marks")),
                   control=JunctionControlParams.preset(cfg["junction_control"].get("preset", "default"),
                                                        **section(cfg, "junction_control")),
                   lights=TrafficLightParams(**section(cfg, "traffic_lights")),
                   turn_lanes=TurnLaneParams(**section(cfg, "turn_lanes")))
