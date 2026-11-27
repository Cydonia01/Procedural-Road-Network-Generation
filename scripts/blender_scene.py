"""Open a KADMOS .glb in Blender with light, sky and a camera; save a .blend and/or render.

    blender -b --python scripts/blender_scene.py -- NET.glb [--blend OUT.blend] [--render OUT.png]
            [--engine eevee|cycles] [--samples N] [--size W H] [--camera X,Y,Z,TX,TY,TZ]
    blender --python scripts/blender_scene.py -- NET.glb          # the same, left open in the UI

(Flatpak: ``flatpak run org.blender.Blender`` for ``blender``; paths absolute,
the sandbox does not see /tmp.)

The .glb carries everything else: geometry in OpenDRIVE axes around the
origin in its extras (``kadmos_origin``), materials with their embedded
textures (scripts/fetch_textures.py, then `export-gltf` / `pipeline --gltf`).
This script adds what glTF has no place for:

- a sun and a sky (world),
- a camera looking at the whole network from the south-west, or from --camera
  (position and target in the .glb's coordinates [m]),
- clip distances fitting a city several kilometres wide, in the camera and in
  every 3D view, and those views in Material Preview (Solid shading shows no
  textures),
- the render settings (engine, samples, size).
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Optional

SKY_TYPES = ("MULTIPLE_SCATTERING", "NISHITA", "SINGLE_SCATTERING", "HOSEK_WILKIE")
ENGINES = {"eevee": ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"), "cycles": ("CYCLES",)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    """The script's own arguments: those after "--" on Blender's command line."""
    own = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser(prog="blender_scene.py", description=__doc__.splitlines()[0])
    p.add_argument("glb", help=".glb written by export-gltf or pipeline --gltf")
    p.add_argument("--blend", help="save the scene as this .blend")
    p.add_argument("--render", help="render one image to this .png")
    p.add_argument("--engine", choices=sorted(ENGINES), default="eevee", help="render engine (default %(default)s)")
    p.add_argument("--samples", type=int, default=64, help="render samples (default %(default)s)")
    p.add_argument("--size", type=int, nargs=2, default=(1920, 1080), metavar=("W", "H"),
                   help="render size [px] (default 1920 1080)")
    p.add_argument("--camera", help="X,Y,Z,TX,TY,TZ: camera position and target [m] (default: overview)")
    args = p.parse_args(own)
    if args.camera is not None:
        values = [float(v) for v in args.camera.split(",")]
        if len(values) != 6:
            p.error("--camera expects X,Y,Z,TX,TY,TZ")
        args.camera = values
    return args


def overview(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> list[float]:
    """Camera position and target [m] seeing a bounding box from the south-west, 35 degrees
    down: target its centre, distance about its diagonal."""
    cx, cy, cz = ((a + b) / 2 for a, b in zip(lo, hi))
    size = max(math.hypot(hi[0] - lo[0], hi[1] - lo[1]), 1.0)
    d = 0.8 * size
    pitch, bearing = math.radians(35), math.radians(45)
    flat = d * math.cos(pitch)
    return [cx - flat * math.cos(bearing), cy - flat * math.sin(bearing), cz + d * math.sin(pitch), cx, cy, cz]


def main(argv: Optional[list[str]] = None) -> None:
    import bpy
    from mathutils import Vector

    args = parse_args(sys.argv if argv is None else argv)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.glb)
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"{args.glb}: no meshes")
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = tuple(min(c[k] for c in corners) for k in range(3))
    hi = tuple(max(c[k] for c in corners) for k in range(3))
    size = max(hi[0] - lo[0], hi[1] - lo[1], 1.0)

    # sky and sun
    world = bpy.data.worlds.new("kadmos sky")
    world.use_nodes = True
    nodes = world.node_tree.nodes
    background = nodes["Background"]
    sky = nodes.new("ShaderNodeTexSky")
    available = {item.identifier for item in sky.bl_rna.properties["sky_type"].enum_items}
    sky.sky_type = next(t for t in SKY_TYPES if t in available)
    if hasattr(sky, "sun_disc"):
        sky.sun_disc = False
    if hasattr(sky, "sun_elevation"):
        sky.sun_elevation = math.radians(40)
    world.node_tree.links.new(sky.outputs["Color"], background.inputs["Color"])
    background.inputs["Strength"].default_value = 0.7
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 4.0
    sun.data.angle = math.radians(1.0)
    sun.rotation_euler = (math.radians(50), 0.0, math.radians(40))
    scene.collection.objects.link(sun)

    # camera
    x, y, z, tx, ty, tz = args.camera or overview(lo, hi)
    cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    cam.location = (x, y, z)
    cam.rotation_euler = (Vector((tx, ty, tz)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 35
    cam.data.clip_start, cam.data.clip_end = 0.1, 4 * size
    scene.collection.objects.link(cam)
    scene.camera = cam
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.clip_start, space.clip_end = 0.1, 4 * size
                    space.shading.type = "MATERIAL"  # textures and colours; Solid shows grey clay

    # render settings
    for engine in ENGINES[args.engine]:  # render engines register at run time: try their names
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    if args.engine == "cycles":
        scene.cycles.samples = args.samples
    elif hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = args.samples
    scene.render.resolution_x, scene.render.resolution_y = args.size
    print(f"kadmos scene: {len(meshes)} objects, {size:.0f} m wide, engine {scene.render.engine}, "
          f"origin {list(scene.get('kadmos_origin', []))}")

    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=args.blend)
        print(f"saved {args.blend}")
    if args.render:
        scene.render.filepath = args.render
        bpy.ops.render.render(write_still=True)
        print(f"rendered {args.render}")


if __name__ == "__main__":
    main()
