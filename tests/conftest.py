import pytest

from roadgen.history import ENV_VAR


@pytest.fixture(autouse=True)
def _no_run_folders(monkeypatch):
    """Tests call cli.main: don't record their runs in out/<hash>/ (-o stays a path).
    tests/test_history.py turns recording on in a temporary folder."""
    monkeypatch.setenv(ENV_VAR, "off")
