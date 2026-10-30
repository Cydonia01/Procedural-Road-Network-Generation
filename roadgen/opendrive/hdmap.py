"""HD-map settings of the OpenDRIVE builder (stage B).

One bundle the builder takes next to DesignParams and JunctionParams. With
enabled off, the builder writes exactly what it wrote before stage B (the
builder identity guard, tests/test_hdmap_identity.py). Later phases add
road marks, junction control, lights and turn lanes here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from roadgen.config import DEFAULTS, section
from roadgen.geometry.cross_section import CrossSectionParams

_D = section(DEFAULTS, "hdmap")


@dataclass(frozen=True)
class HdMapParams:
    enabled: bool = _D["enabled"]  # off: no stage B content
    seed: int = DEFAULTS["seed"]  # random choices (stop share, light offsets, ...), salted per feature
    cross_section: CrossSectionParams = field(default_factory=CrossSectionParams)

    @property
    def active_cross_section(self) -> Optional[CrossSectionParams]:
        """The cross-section the geometry uses, None with HD maps off."""
        return self.cross_section if self.enabled else None

    @classmethod
    def from_config(cls, cfg: dict, seed: Optional[int] = None, enabled: Optional[bool] = None) -> HdMapParams:
        """From a config's hdmap and cross_section sections; enabled (e.g. --hd / --no-hd)
        overrides hdmap.enabled when given."""
        on = section(cfg, "hdmap")["enabled"] if enabled is None else enabled
        return cls(enabled=on, seed=cfg["seed"] if seed is None else seed,
                   cross_section=CrossSectionParams(**section(cfg, "cross_section")))
