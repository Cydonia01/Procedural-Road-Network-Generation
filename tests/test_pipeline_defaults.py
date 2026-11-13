"""Polish N3: what `pipeline` writes and checks by default (defaults.yaml `pipeline:`),
and the --no-... switches. The other tests run with these defaults off (conftest.py)."""

from __future__ import annotations

import logging

import pytest

from cli import main
from roadgen.config import DEFAULTS
from tests.grids import ON_MAPS, grid_cli


def _rm_available() -> bool:
    try:
        from roadgen.sim.esmini import esmini_library

        esmini_library("esminiRMLib")
        return True
    except FileNotFoundError:
        return False


@pytest.mark.pipeline_defaults
def test_defaults_are_on():
    assert DEFAULTS["pipeline"] == {"buildings": True, "ground": True, "lane_plot": True, "drive_check": True}


@pytest.mark.pipeline_defaults
@pytest.mark.skipif(not _rm_available(), reason="esmini RoadManager library not found")
def test_pipeline_writes_everything_by_default(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="roadgen.cli")
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", "lsystem", *grid_cli(3, 3), *ON_MAPS, "--maps", "-o", str(out)]) == 0
    assert (tmp_path / "net_scene.osgt").is_file() and (tmp_path / "net_hd.png").is_file()
    assert 'Name "buildings"' in (tmp_path / "net_scene.osgt").read_text()
    assert any("movements drove through correctly" in r.getMessage() for r in caplog.records)


@pytest.mark.pipeline_defaults
def test_switches_turn_them_off_and_ground_needs_an_elevation_map(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="roadgen.cli")
    out = tmp_path / "net"
    assert main(["--no-history", "pipeline", "lsystem", *grid_cli(3, 3), *ON_MAPS, "--maps", "--no-buildings",
                 "--no-ground", "--no-lane-plot", "--no-drive-check", "-o", str(out)]) == 0
    assert not list(tmp_path.glob("net_*.osgt")) and not (tmp_path / "net_hd.png").exists()
    assert not any("movements drove through" in r.getMessage() for r in caplog.records)
    # Without maps the default ground is left out (buildings alone); asked for, it is an error.
    flat = tmp_path / "flat"
    assert main(["--no-history", "pipeline", "lsystem", *grid_cli(3, 3), "--no-drive-check", "--no-lane-plot",
                 "-o", str(flat)]) == 0
    assert (tmp_path / "flat_buildings.osgt").is_file() and not (tmp_path / "flat_scene.osgt").exists()
    with pytest.raises(SystemExit, match="--ground needs an elevation map"):
        main(["--no-history", "pipeline", "lsystem", *grid_cli(3, 3), "--ground", "-o", str(flat)])
