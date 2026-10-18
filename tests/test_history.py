"""Run history: hashes of successful commands, deduplicated, replayable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli import main
from roadgen.history import ENV_VAR, History

GEN = ["generate", "manhattan", "--avenues", "3", "--streets", "2"]


@pytest.fixture
def history(tmp_path, monkeypatch) -> History:
    path = tmp_path / "runs" / "history.json"
    monkeypatch.setenv(ENV_VAR, str(path))
    return History(path)


def test_successful_run_is_recorded_once(history, tmp_path):
    out = str(tmp_path / "g.json")
    assert main([*GEN, "-o", out]) == 0
    assert main([*GEN, "-o", out]) == 0
    runs = history.runs()
    assert len(runs) == 1
    assert runs[0].argv == [*GEN, "-o", out]
    assert len(runs[0].hash) == 12


def test_same_inputs_spelled_differently_share_a_hash(history, tmp_path):
    out = str(tmp_path / "g.json")
    main([*GEN, "-o", out])
    # seed 0 is the default; -v and the note don't change the result
    main(["-v", "--note", "same", *GEN, "--seed", "0", "-o", out])
    main([*GEN[:2], "--streets", "2", "--avenues", "3", "-o", out])
    runs = history.runs()
    assert len(runs) == 1 and runs[0].note == "same"
    main([*GEN, "--seed", "1", "-o", out])
    assert len(history.runs()) == 2


def test_failed_and_listing_commands_are_not_recorded(history, tmp_path):
    with pytest.raises(SystemExit):
        main(["generate", "manhattan", "-o", str(tmp_path / "g.json")])  # needs --avenues/--streets
    with pytest.raises(SystemExit):
        main(["generate", "manhattan", "--bogus"])
    assert main(["drive-check", str(tmp_path / "missing.xodr")]) == 1  # returns an error
    main(["generators"])
    main(["maps"])
    main(["history"])
    assert history.runs() == []


def test_no_history_flag_and_env_off(history, tmp_path, monkeypatch):
    main(["--no-history", *GEN, "-o", str(tmp_path / "a.json")])
    assert history.runs() == []
    monkeypatch.setenv(ENV_VAR, "off")
    main([*GEN, "-o", str(tmp_path / "b.json")])
    assert not history.path.exists()


def test_replay_by_hash_reproduces_the_output(history, tmp_path):
    out = tmp_path / "g.json"
    main([*GEN, "--seed", "4", "--jitter", "2", "-o", str(out)])
    first = out.read_text()
    out.unlink()
    h = history.runs()[0].hash
    assert main([h]) == 0  # bare hash
    assert out.read_text() == first
    out.unlink()
    assert main(["replay", h[:6]]) == 0  # unique prefix
    assert out.read_text() == first
    assert len(history.runs()) == 1


def test_replay_uses_the_recorded_config(history, tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("generators:\n  manhattan:\n    lane_width: 4.25\n")
    out = tmp_path / "g.json"
    main(["--config", str(cfg), *GEN, "-o", str(out)])
    main([*GEN, "-o", str(out)])  # other config: other run
    runs = history.runs()
    assert len(runs) == 2
    assert runs[0].config_file == str(cfg) and "--config" not in runs[0].argv
    assert runs[0].config["generators"]["manhattan"]["lane_width"] == 4.25
    cfg.unlink()  # the config file is gone; the history has its content
    assert main([runs[0].hash]) == 0
    assert {e["lane_width"] for e in json.loads(out.read_text())["edges"]} == {4.25}


def test_replay_errors(history, capsys):
    with pytest.raises(SystemExit, match="no run"):
        main(["replay", "abcdef"])
    with pytest.raises(SystemExit, match="too short"):
        main(["replay", "abc"])


def test_replay_show_prints_the_command(history, tmp_path, capsys):
    main(["--note", "demo", *GEN, "-o", str(tmp_path / "g.json")])
    h = history.runs()[0].hash
    capsys.readouterr()
    assert main(["replay", h, "--show"]) == 0
    assert capsys.readouterr().out.strip() == "python -m cli " + " ".join(
        [*GEN, "-o", str(tmp_path / "g.json")])
    main(["history", "--grep", "demo"])
    assert h in capsys.readouterr().out


def test_history_file_is_valid_json(history, tmp_path):
    main([*GEN, "-o", str(tmp_path / "g.json")])
    data = json.loads(Path(history.path).read_text())
    assert data["schema_version"] == 1
    (run,) = data["runs"].values()
    assert run["config"] in data["configs"] and "history" not in data["configs"][run["config"]]
