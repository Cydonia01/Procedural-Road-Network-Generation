import pytest

from roadgen.history import ENV_VAR


@pytest.fixture(autouse=True)
def _private_run_history(tmp_path, monkeypatch):
    """Tests call cli.main; keep their runs out of the project's runs/history.json."""
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "history.json"))
