"""Download the textures the Blender export (.glb) embeds: run once per checkout.

    python scripts/fetch_textures.py [--dest assets/textures] [--resolution 1K] [--force]

Two free sources, nothing else needed (Python standard library only):

- **ambientCG** (ambientcg.com, CC0): surface textures for the scene's
  materials (asphalt, concrete, grass, ground, facade, roof gravel); per
  texture its colour, OpenGL normal and roughness maps from the
  ``<ID>_<res>-JPG.zip`` download, and its real size [m] for tiling.
- **Wikimedia Commons** (German traffic signs, public domain as official
  works, § 5 UrhG): one PNG (rendered from the SVG) per sign the OpenDRIVE
  builder writes, named after the sign's material in the .glb
  (sign_DE_<type>[_<subtype>][_<value>]).

Writes ``<dest>/<texture>/{color,normal,roughness}.jpg``,
``<dest>/signs/<material>.png`` and ``<dest>/manifest.json`` (which material
uses which files, tile sizes, sources and licences), which the export reads
(gltf.textures). The choice of assets is fixed here, so every checkout gets
the same textures. Files already there are kept unless --force.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("fetch_textures")

USER_AGENT = "kadmos-fetch-textures/1.0 (procedural road networks; texture download)"
AMBIENTCG = "https://ambientcg.com/get?file={asset}_{res}-JPG.zip"
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/{file}?width={width}"
CC0 = "CC0 1.0 (ambientCG)"
PD_SIGN = "public domain: German official work (§ 5 UrhG), Wikimedia Commons"

# texture name -> (ambientCG asset id, tile size [m] along u, v)
TEXTURES: dict[str, tuple[str, tuple[float, float]]] = {
    "asphalt": ("Asphalt033", (2.5, 2.5)),
    "concrete": ("Concrete049", (1.1, 1.1)),
    "paving": ("Concrete034", (1.1, 0.55)),
    "grass": ("Grass004", (1.4, 1.4)),
    "ground": ("Ground037", (2.1, 2.1)),
    "facade": ("Facade018A", (13.0, 13.0)),
    "gravel": ("Gravel023", (1.5, 1.5)),
}
# .glb material -> texture name
MATERIALS: dict[str, str] = {
    "lane_driving": "asphalt", "lane_shoulder": "asphalt", "lane_other": "asphalt",
    "lane_border": "concrete", "lane_sidewalk": "paving", "lane_median": "grass",
    "concrete": "concrete", "ground_land": "ground", "ground_vegetation": "grass",
    "building_wall": "facade", "building_roof": "gravel",
}
# ambientCG map name in the zip -> our file name
MAPS = {"Color": "color.jpg", "NormalGL": "normal.jpg", "Roughness": "roughness.jpg"}

_SPEED = "Zeichen 274-{v} - Zulässige Höchstgeschwindigkeit, StVO 2017.svg"
# sign material -> Wikimedia Commons file
SIGNS: dict[str, str] = {
    "sign_DE_206": "Zeichen 206 - Halt! Vorfahrt gewähren! StVO 2017.svg",
    "sign_DE_205": "Zeichen 205 - Vorfahrt gewähren! StVO 1970.svg",
    "sign_DE_301": "Zeichen 301 - Vorfahrt, StVO 1970.svg",
    "sign_DE_306": "Zeichen 306 - Vorfahrtstraße, StVO 1970.svg",
    "sign_DE_307": "Zeichen 307 - Ende der Vorfahrtstraße, StVO 1981.svg",
    "sign_DE_103_10": "Zeichen 103-10 - Kurve (links), StVO 1992.svg",
    "sign_DE_103_20": "Zeichen 103-20 - Kurve (rechts), StVO 1992.svg",
    "sign_DE_105_10": "Zeichen 105-10 - Doppelkurve (zunächst links), StVO 1992.svg",
    "sign_DE_105_20": "Zeichen 105-20 - Doppelkurve (zunächst rechts), StVO 1992.svg",
    "sign_DE_209_10": "Zeichen 209-10 - Vorgeschriebene Fahrtrichtung, links, StVO 2017.svg",
    "sign_DE_209_20": "Zeichen 209 - Vorgeschriebene Fahrtrichtung, rechts, StVO 2017.svg",
    "sign_DE_209_30": "Zeichen 209-30 - Vorgeschriebene Fahrtrichtung, Geradeaus, StVO 2017.svg",
    "sign_DE_214": "Zeichen 214 - Vorgeschriebene Fahrtrichtung, Geradeaus oder rechts, StVO 2017.svg",
    "sign_DE_214_10": "Zeichen 214-10 - Vorgeschriebene Fahrtrichtung, Geradeaus oder links, StVO 2017.svg",
    "sign_DE_214_30": "Zeichen 214-30 - Vorgeschriebene Fahrtrichtung, Rechts oder links, StVO 2017.svg",
    "sign_DE_215": "Zeichen 215 - Kreisverkehr, StVO 2000.svg",
    "sign_DE_350_10": "Zeichen 350-10 - Fußgängerüberweg (rechts), StVO 1992.svg",
    "sign_DE_350_20": "Zeichen 350-20 - Fußgängerüberweg (Linksaufstellung) einseitig, StVO 1992.svg",
    **{f"sign_DE_274_{v}": _SPEED.format(v=v) for v in range(10, 140, 10)},
}

Fetch = Callable[[str], bytes]


def http_get(url: str, tries: int = 4, pause: float = 2.0) -> bytes:
    """The body of url, retried with growing pauses (Commons rate-limits quick requests)."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except OSError as err:
            if attempt == tries - 1:
                raise
            log.warning("%s: %s; retrying", url, err)
            time.sleep(pause * (attempt + 1))
    raise RuntimeError("unreachable")


def ambientcg_url(asset: str, resolution: str) -> str:
    return AMBIENTCG.format(asset=asset, res=resolution)


def commons_url(file: str, width: int) -> str:
    return COMMONS.format(file=urllib.parse.quote(file.replace(" ", "_")), width=width)


def extract_maps(data: bytes, asset: str, resolution: str) -> dict[str, bytes]:
    """Our file name -> bytes of the maps in an ambientCG zip (KeyError if one is missing)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        out = {}
        for key, ours in MAPS.items():
            name = f"{asset}_{resolution}-JPG_{key}.jpg"
            if name not in names:
                raise KeyError(f"{name} not in the {asset} download ({sorted(names)})")
            out[ours] = z.read(name)
        return out


def manifest(resolution: str, signs: dict[str, str]) -> dict:
    """The manifest the export reads: textures (files, tile size, source, licence), which
    material uses which texture, and the sign images that were downloaded."""
    return {
        "resolution": resolution,
        "textures": {
            name: {"files": {k.split(".")[0]: f"{name}/{k}" for k in MAPS.values()}, "tile": list(tile),
                   "source": f"https://ambientcg.com/view?id={asset}", "license": CC0}
            for name, (asset, tile) in TEXTURES.items()
        },
        "materials": dict(MATERIALS),
        "signs": {name: {"file": f"signs/{name}.png", "source": f"https://commons.wikimedia.org/wiki/File:"
                                                               f"{urllib.parse.quote(f.replace(' ', '_'))}",
                         "license": PD_SIGN} for name, f in sorted(signs.items())},
    }


def fetch(dest: Path, resolution: str = "1K", force: bool = False, sign_width: int = 512,
          get: Fetch = http_get, pause: float = 1.0) -> dict:
    """Download everything into dest and write the manifest; returns it. A sign that fails
    is left out (logged); a texture that fails stops the run."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, (asset, _) in TEXTURES.items():
        folder = dest / name
        if not force and all((folder / f).is_file() for f in MAPS.values()):
            log.info("%s: kept (%s)", name, asset)
            continue
        log.info("%s: downloading %s", name, asset)
        folder.mkdir(exist_ok=True)
        for file, data in extract_maps(get(ambientcg_url(asset, resolution)), asset, resolution).items():
            (folder / file).write_bytes(data)
    got: dict[str, str] = {}
    (dest / "signs").mkdir(exist_ok=True)
    for name, file in SIGNS.items():
        path = dest / "signs" / f"{name}.png"
        if force or not path.is_file():
            try:
                data = get(commons_url(file, sign_width))
            except OSError as err:
                log.error("%s: %s (left out)", name, err)
                continue
            if not data.startswith(b"\x89PNG"):
                log.error("%s: not a PNG (left out)", name)
                continue
            path.write_bytes(data)
            time.sleep(pause)  # be gentle with Commons
        got[name] = file
    out = manifest(resolution, got)
    (dest / "manifest.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log.info("wrote %s: %d textures, %d signs", dest / "manifest.json", len(TEXTURES), len(got))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Download the CC0 textures and sign images for the .glb export")
    p.add_argument("--dest", default="assets/textures", help="target folder (default %(default)s)")
    p.add_argument("--resolution", default="1K", choices=["1K", "2K", "4K"],
                   help="ambientCG resolution (default %(default)s)")
    p.add_argument("--force", action="store_true", help="download again what is already there")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        fetch(Path(args.dest), args.resolution, args.force)
    except (OSError, KeyError, zipfile.BadZipFile) as err:
        log.error("%s", err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
