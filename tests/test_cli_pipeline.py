"""The pipeline end to end: one city on generated maps with every output, run once.

The functions behind each stage and flag are tested on their own in their modules; this
file checks that the CLI runs them all on one network and writes what each promises
(maps, graph, plot, .xodr and sidecar, lane plot, scene with ground and buildings) with
every stage's check passing. Small runs check what only a second run can show (--no-hd,
--flat).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from cli import main
from roadgen.generators.lsystem import LSystemGenerator, LSystemParams
from roadgen.graph.io import load_json
from roadgen.graph.model import RoadGraph
from roadgen.maps.model import load_npz
from roadgen.opendrive import HDMAP_VERSION
from roadgen.validate.hdmap import check_hdmap
from tests.grids import grid_cli
from tests.shared import grown

GROW = ["--preset", "city", "--set", "extent=[1500, 900]", "--seed", "3"]
CITY = [*GROW, "--allow-traps"]
KINDS = ("terrain", "elevation", "population")


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture(scope="module")
def city(tmp_path_factory) -> tuple[Path, list[str]]:
    """`pipeline lsystem --preset city --maps --ground --lane-plot` (HD maps by default):
    the output prefix and the log messages."""
    prefix = tmp_path_factory.mktemp("e2e") / "city"
    records, root = _Records(), logging.getLogger()
    level = root.level
    root.addHandler(records)
    root.setLevel(logging.INFO)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("OPENDRIVE_XSD", raising=False)
            code = main(["--no-history", "pipeline", "lsystem", *CITY, "--maps", "--ground", "--buildings",
                         "--lane-plot", "--no-drive-check", "-o", str(prefix)])
    finally:
        root.removeHandler(records)
        root.setLevel(level)
    assert code == 0, [m for m in records.messages if "error" in m.lower()]
    return prefix, records.messages


@pytest.mark.slow
def test_writes_every_output(city):
    prefix, _ = city
    out = prefix.parent
    for suffix in (".json", ".png", ".xodr", ".roadmap.json"):
        assert prefix.with_suffix(suffix).is_file(), suffix
    for name in ("city_hd.png", "city_scene.osgt", *(f"city_{k}.npz" for k in KINDS), *(f"city_{k}.png" for k in KINDS)):
        assert (out / name).is_file(), name
    # city grows with the grid goal: no style map; with --ground the buildings are in the scene
    assert not (out / "city_style.npz").exists() and not (out / "city_buildings.osgt").exists()
    text = (out / "city_scene.osgt").read_text()
    assert 'Name "ground"' in text and 'Name "buildings"' in text


@pytest.mark.slow
def test_every_stage_is_checked_and_passes(city):
    _, messages = city
    (line,) = [m for m in messages if m.startswith("validation: ")]
    parts = line[len("validation: "):].split("; ")
    names = [part.rsplit(" ", 1)[0] for part in parts]
    assert names[:5] == ["map terrain", "map elevation", "map population", "maps", "generate"]
    assert {"align", "drape", "annotate", "fit", "xodr", "scene"} <= set(names)
    assert not [p for p in parts if "errors" in p], line


@pytest.mark.slow
def test_ground_covers_no_road_surface(city):
    # Quality gate (F14) on a whole generated city: the scene stage warns about every
    # road-surface sample under the ground (stages.check_scene). The scene is built in a
    # forked process beside the lane plot, so its report, not its log, comes back.
    _, messages = city
    (line,) = [m for m in messages if m.startswith("validation: ")]
    assert "; scene ok" in line, line


@pytest.mark.slow
def test_graph_carries_every_stage(city):
    prefix, _ = city
    g = load_json(prefix.with_suffix(".json"))
    assert g.has_elevation and {"drape", "annotate", "bridges"} <= set(g.metadata)
    assert set(g.metadata["maps"]) == set(KINDS)
    assert {e.cross_section for e in g.edges.values()} - {None}  # annotate set the profiles
    bounds = LSystemGenerator().map_bounds(LSystemParams.preset("city", extent=(1500.0, 900.0)))
    for kind in KINDS:
        assert load_npz(prefix.parent / f"city_{kind}.npz").grid.covers(bounds)


@pytest.mark.slow
def test_xodr_has_the_hd_map(city):
    prefix, _ = city
    assert check_hdmap(prefix.with_suffix(".xodr")) == []
    assert "<signal " in prefix.with_suffix(".xodr").read_text()  # signs and lights
    assert json.loads(prefix.with_suffix(".roadmap.json").read_text())["hdmap_version"] == HDMAP_VERSION


def _plan(g: RoadGraph) -> tuple:
    """The plan view: nodes and roads without the heights (generate drapes on an elevation map)."""
    return ({k: (n.x, n.y) for k, n in g.nodes.items()},
            {k: (e.from_node, e.to_node, e.road_class, e.lanes_forward, e.lanes_backward) for k, e in g.edges.items()})


@pytest.mark.slow
def test_commands_rerun_stages_on_its_files(city, tmp_path):
    prefix, _ = city
    files = [f"--map-file={k}={prefix.parent / f'city_{k}.npz'}" for k in KINDS]
    # annotate on the written graph and population map
    assert main(["--no-history", "annotate", str(prefix.with_suffix(".json")), files[2],
                 "-o", str(tmp_path / "a.json")]) == 0
    # the same maps from files grow the network lsystem grew on --maps (which
    # tests/shared.py builds as the pipeline does)
    out = tmp_path / "files.json"
    assert main(["--no-history", "generate", "lsystem", *GROW, *files, "-o", str(out)]) == 0
    again = load_json(out)
    assert _plan(again) == _plan(grown("city", 3, maps=True, extent=(1500.0, 900.0)))
    assert again.metadata["maps"]["terrain"]["source"].endswith("city_terrain.npz")


def test_no_hd_is_the_plain_network(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDRIVE_XSD", raising=False)
    on, off = tmp_path / "on", tmp_path / "off"
    base = ["--no-history", "pipeline", "lsystem", *grid_cli(3, 3), "--allow-traps"]
    assert main([*base, "--lane-plot", "-o", str(on)]) == 0
    assert main([*base, "--no-hd", "--lane-plot", "-o", str(off)]) == 0
    assert "hdmap_version" not in json.loads(off.with_suffix(".roadmap.json").read_text())
    assert "<signal " not in off.with_suffix(".xodr").read_text()
    assert on.with_suffix(".xodr").read_bytes() != off.with_suffix(".xodr").read_bytes()
    assert (tmp_path / "on_hd.png").is_file() and not (tmp_path / "off_hd.png").exists()


def test_default_output_goes_to_out(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["--no-history", "pipeline", "lsystem", *grid_cli(2, 2)]) == 0
    assert (tmp_path / "out" / "lsystem_s0.xodr").is_file()
