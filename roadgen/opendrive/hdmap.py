"""HD-map settings of the OpenDRIVE builder (stage B).

One bundle the builder takes next to DesignParams and JunctionParams. With
enabled off, the builder writes exactly what it wrote before stage B (the
builder identity guard, tests/test_hdmap_identity.py). It holds the
cross-section and the road marks; later phases add junction control, lights
and turn lanes here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from roadgen.config import DEFAULTS, section
from roadgen.geometry.cross_section import CrossSectionParams
from roadgen.opendrive.marks import RoadMarkParams

_D = section(DEFAULTS, "hdmap")


@dataclass(frozen=True)
class HdMapParams:
    enabled: bool = _D["enabled"]  # off: no stage B content
    seed: int = DEFAULTS["seed"]  # random choices (stop share, light offsets, ...), salted per feature
    cross_section: CrossSectionParams = field(default_factory=CrossSectionParams)
    marks: RoadMarkParams = field(default_factory=RoadMarkParams)

    @property
    def active_cross_section(self) -> Optional[CrossSectionParams]:
        """The cross-section the geometry uses, None with HD maps off."""
        return self.cross_section if self.enabled else None

    @property
    def active_marks(self) -> Optional[RoadMarkParams]:
        """The HD road marks, None with HD maps or road_marks off (plain marks)."""
        return self.marks if self.enabled and self.marks.enabled else None

    @classmethod
    def from_config(cls, cfg: dict, seed: Optional[int] = None, enabled: Optional[bool] = None) -> HdMapParams:
        """From a config's hdmap, cross_section and road_marks sections; enabled (e.g. --hd / --no-hd)
        overrides hdmap.enabled when given."""
        on = section(cfg, "hdmap")["enabled"] if enabled is None else enabled
        return cls(enabled=on, seed=cfg["seed"] if seed is None else seed,
                   cross_section=CrossSectionParams(**section(cfg, "cross_section")),
                   marks=RoadMarkParams(**section(cfg, "road_marks")))
