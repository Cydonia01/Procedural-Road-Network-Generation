import pytest
import yaml

import roadgen.config
from roadgen.history import ENV_VAR

# What `pipeline` adds by default (defaults.yaml `pipeline:`): off in tests, which ask
# for it with --buildings, --ground, ... where they need it (the drive-check alone would
# run esmini on every pipeline). tests/test_pipeline_defaults.py checks the real defaults.
PIPELINE_OFF = {"buildings": False, "ground": False, "lane_plot": False, "drive_check": False}


@pytest.fixture(autouse=True)
def _no_run_folders(monkeypatch):
    """Tests call cli.main: don't record their runs in out/<hash>/ (-o stays a path).
    tests/test_history.py turns recording on in a temporary folder."""
    monkeypatch.setenv(ENV_VAR, "off")


@pytest.fixture(autouse=True)
def _pipeline_outputs_off(monkeypatch, tmp_path_factory, request):
    """The pipeline's default outputs off (see PIPELINE_OFF), in the loaded config and in
    DEFAULTS alike, so run records don't list them as changed."""
    if request.node.get_closest_marker("pipeline_defaults"):
        return
    path = tmp_path_factory.getbasetemp() / "defaults_pipeline_off.yaml"
    if not path.exists():
        data = yaml.safe_load(roadgen.config.DEFAULTS_PATH.read_text())
        data["pipeline"].update(PIPELINE_OFF)
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setattr(roadgen.config, "DEFAULTS_PATH", path)
    for key, value in PIPELINE_OFF.items():
        monkeypatch.setitem(roadgen.config.DEFAULTS["pipeline"], key, value)
