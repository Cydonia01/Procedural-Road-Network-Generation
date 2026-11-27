"""The standalone scripts: scripts/fetch_textures.py (without the network) and
scripts/blender_scene.py (its arguments; Blender itself is not in the container)."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

from kadmos.config import DEFAULTS, section
from kadmos.opendrive.furniture import LIGHT_TYPE
from kadmos.scene.signals import SIGNS
from kadmos.scene.textures import load_library

ROOT = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fetch = _load("fetch_textures")
blender = _load("blender_scene")


def _zip(asset: str, res: str, maps=("Color", "NormalGL", "Roughness", "AmbientOcclusion")) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for m in maps:
            z.writestr(f"{asset}_{res}-JPG_{m}.jpg", f"{asset}-{m}")
    return buf.getvalue()


def test_materials_are_ones_the_export_writes():
    lanes = {f"lane_{k}" for k in section(DEFAULTS, "gltf", "lane_colors")}
    scene = {"concrete", "ground_land", "ground_vegetation", "building_wall", "building_roof"}
    assert set(fetch.MATERIALS) <= lanes | scene
    assert set(fetch.MATERIALS.values()) <= set(fetch.TEXTURES)
    # every sign type the export shapes, and every speed the builder posts (10 to 130 km/h)
    types = {name.split("_")[2] for name in fetch.SIGNS}
    assert set(SIGNS) <= types and LIGHT_TYPE not in types
    assert {f"sign_DE_274_{v}" for v in range(10, 140, 10)} <= set(fetch.SIGNS)


def test_extract_maps_picks_colour_normal_roughness():
    files = fetch.extract_maps(_zip("Asphalt033", "1K"), "Asphalt033", "1K")
    assert files == {"color.jpg": b"Asphalt033-Color", "normal.jpg": b"Asphalt033-NormalGL",
                     "roughness.jpg": b"Asphalt033-Roughness"}
    with pytest.raises(KeyError, match="NormalGL"):
        fetch.extract_maps(_zip("X", "1K", ("Color", "Roughness")), "X", "1K")


def test_fetch_writes_a_manifest_the_export_reads(tmp_path, monkeypatch):
    monkeypatch.delenv("KADMOS_TEXTURES")
    asked: list[str] = []

    def get(url: str) -> bytes:
        asked.append(url)
        if "ambientcg" in url:
            asset = url.split("file=")[1].split("_")[0]
            return _zip(asset, "1K")
        return b"<html>not found</html>" if "206" in url else b"\x89PNG fake"

    out = fetch.fetch(tmp_path, get=get, pause=0)
    assert "sign_DE_206" not in out["signs"] and "sign_DE_205" in out["signs"]  # not a PNG: left out
    assert (tmp_path / "asphalt" / "normal.jpg").read_bytes() == b"Asphalt033-NormalGL"
    assert json.loads((tmp_path / "manifest.json").read_text()) == out
    lib = load_library(tmp_path)
    assert lib.materials["lane_driving"].tile == (2.5, 2.5) and "sign_DE_205" in lib.signs
    n = len(asked)
    fetch.fetch(tmp_path, get=get, pause=0)  # kept: only the missing sign is asked for again
    assert len(asked) == n + 1
    assert fetch.commons_url("Zeichen 205 - x.svg", 512).endswith("Zeichen_205_-_x.svg?width=512")


def test_blender_script_arguments():
    args = blender.parse_args(["blender", "-b", "--python", "x.py", "--", "net.glb", "--render", "a.png",
                               "--camera=-1,2,3,4,5,6"])
    assert args.glb == "net.glb" and args.render == "a.png" and args.camera == [-1, 2, 3, 4, 5, 6]
    assert args.engine == "eevee" and args.blend is None
    with pytest.raises(SystemExit):
        blender.parse_args(["--", "net.glb", "--camera", "1,2,3"])
    x, y, z, tx, ty, tz = blender.overview((0, 0, 0), (100, 100, 10))
    assert (tx, ty, tz) == (50, 50, 5) and x < tx and y < ty and z > tz
