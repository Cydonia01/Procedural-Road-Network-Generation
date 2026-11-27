"""The texture library the .glb embeds: what scripts/fetch_textures.py downloaded.

``<dir>/manifest.json`` says which material uses which texture (colour,
OpenGL normal and roughness maps, and the size one tile covers [m]) and which
sign images there are. Without the folder or its manifest the export keeps
plain colours. Only paths are read here; the writer (kadmos.scene.gltf)
embeds the files as they are.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Union

from kadmos.scene.gltf import Material

log = logging.getLogger(__name__)

TEXTURES_ENV = "KADMOS_TEXTURES"  # overrides gltf.textures: a folder, or "off" for plain colours


@dataclass(frozen=True)
class TextureSet:
    color: Path
    normal: Optional[Path] = None
    roughness: Optional[Path] = None
    tile: tuple[float, float] = (1.0, 1.0)  # what one tile covers along u, v [m]


@dataclass(frozen=True)
class TextureLibrary:
    materials: dict[str, TextureSet] = field(default_factory=dict)  # material -> its textures
    signs: dict[str, Path] = field(default_factory=dict)  # sign face material -> image

    @property
    def empty(self) -> bool:
        return not self.materials and not self.signs

    def apply(self, material: Material) -> Material:
        """The material with its textures, if the library has any for it (base colour white,
        so the image shows as it is); else unchanged."""
        if material.name in self.signs:
            return replace(material, color=(1.0, 1.0, 1.0), texture=self.signs[material.name], alpha_mask=True)
        t = self.materials.get(material.name)
        if t is None:
            return material
        return replace(material, color=(1.0, 1.0, 1.0), texture=t.color, normal_map=t.normal,
                       roughness_map=t.roughness, tile=t.tile, roughness=1.0 if t.roughness else material.roughness,
                       metallic=0.0)


def load_library(folder: Union[str, Path, None]) -> TextureLibrary:
    """The library in folder (its manifest.json), or in $KADMOS_TEXTURES if set ("off":
    none); empty without one. Entries whose files are missing are left out."""
    folder = os.environ.get(TEXTURES_ENV, folder)
    if not folder or folder == "off":
        return TextureLibrary()
    root = Path(folder)
    path = root / "manifest.json"
    if not path.is_file():
        log.info("no texture library at %s (run scripts/fetch_textures.py): plain colours", root)
        return TextureLibrary()
    data = json.loads(path.read_text())
    sets: dict[str, TextureSet] = {}
    for name, t in data.get("textures", {}).items():
        files = {k: root / v for k, v in t.get("files", {}).items()}
        if "color" not in files or not files["color"].is_file():
            log.warning("texture %s: no colour map, left out", name)
            continue
        tile = tuple(float(v) for v in t.get("tile", (1.0, 1.0)))
        sets[name] = TextureSet(files["color"], *(files.get(k) if files.get(k) and files[k].is_file() else None
                                                  for k in ("normal", "roughness")), tile)  # type: ignore[arg-type]
    materials = {m: sets[t] for m, t in data.get("materials", {}).items() if t in sets}
    signs = {name: root / s["file"] for name, s in data.get("signs", {}).items() if (root / s["file"]).is_file()}
    log.info("texture library %s: %d materials, %d signs", root, len(materials), len(signs))
    return TextureLibrary(materials, signs)
