"""Run folders: one out/<hash>/ per successful command, with its outputs and run.yaml."""

from __future__ import annotations

import json

import pytest
import yaml

from cli import main, run_argv
from roadgen.history import ENV_VAR, RUN_FILE, RunStore

GEN = ["generate", "manhattan", "--avenues", "3", "--streets", "2"]


@pytest.fixture
def store(tmp_path, monkeypatch) -> RunStore:
    root = tmp_path / "out"
    monkeypatch.setenv(ENV_VAR, str(root))
    return RunStore(root)


def run_file(store: RunStore, h: str) -> dict:
    return yaml.safe_load((store.root / h / RUN_FILE).read_text())


def test_run_folder_holds_outputs_and_only_non_default_parameters(store):
    rc, h = run_argv([*GEN, "--seed", "0", "--jitter", "2", "-o", "grid.json"])
    assert rc == 0 and len(h) == 12
    assert sorted(p.name for p in (store.root / h).iterdir()) == ["grid.json", RUN_FILE]
    data = run_file(store, h)
    assert data["command"] == ["generate", "manhattan"]
    assert data["args"] == {"output": "grid.json"}  # --seed 0 is the default
    assert data["config"] == {"generators": {"manhattan": {"n_avenues": 3, "n_streets": 2, "jitter": 2.0}}}
    assert data["cli"].startswith("python -m cli generate manhattan")


def test_same_inputs_spelled_differently_share_a_hash(store, tmp_path):
    _, h = run_argv(GEN)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("generators:\n  manhattan:\n    n_avenues: 3\n")
    for argv in (["-v", "--note", "same", *GEN, "--seed", "0"],
                 [*GEN[:2], "--streets", "2", "--avenues", "3"],
                 [*GEN[:2], "--set", "n_avenues=3", "--set", "n_streets=2", "--lane-width", "3.5"],
                 ["--config", str(cfg), *GEN[:2], "--streets", "2"]):
        assert run_argv(argv)[1] == h
    (run,) = store.runs()
    assert run.note == "same"
    assert run_argv([*GEN, "--seed", "1"])[1] != h
    assert len(store.runs()) == 2


def test_preset_values_are_kept_when_they_differ_from_the_preset(store):
    _, h = run_argv(["generate", "lsystem", "--preset", "city", "--set", "branch_prob=1.0"])
    sec = run_file(store, h)["config"]["generators"]["lsystem"]
    assert sec["preset"] == "city"
    # 1.0 is the default, but city sets its own branch_prob: this run changes it back
    from roadgen.generators.lsystem import LSystemParams
    if LSystemParams.PRESETS["city"].get("branch_prob", 1.0) != 1.0:
        assert sec["branch_prob"] == 1.0


def test_failed_and_listing_commands_leave_no_folder(store, tmp_path):
    with pytest.raises(SystemExit):
        main(["generate", "manhattan", "-o", "g.json"])  # needs --avenues/--streets
    with pytest.raises(SystemExit):
        main(["generate", "manhattan", "--bogus"])
    assert main(["drive-check", str(tmp_path / "missing.xodr")]) == 1  # returns an error
    main(["generators"])
    main(["maps"])
    main(["history"])
    assert store.runs() == []
    assert not store.root.exists() or list(store.root.iterdir()) == []


def test_recorded_runs_take_output_names_not_paths(store, tmp_path):
    with pytest.raises(SystemExit, match="file name"):
        main([*GEN, "-o", str(tmp_path / "g.json")])
    assert store.runs() == []


def test_no_history_flag_and_env_off(store, tmp_path, monkeypatch):
    main(["--no-history", *GEN, "-o", str(tmp_path / "a.json")])
    assert (tmp_path / "a.json").is_file() and store.runs() == []
    monkeypatch.setenv(ENV_VAR, "off")
    main([*GEN, "-o", str(tmp_path / "b.json")])
    assert (tmp_path / "b.json").is_file() and not store.root.exists()


def test_replay_by_hash_and_by_run_file(store):
    _, h = run_argv([*GEN, "--seed", "4", "--jitter", "2", "-o", "g.json"])
    out = store.root / h / "g.json"
    first = out.read_text()
    out.unlink()
    assert main([h]) == 0  # bare hash
    assert out.read_text() == first
    assert main(["replay", h[:6]]) == 0  # unique prefix
    assert main(["run", str(store.root / h / RUN_FILE)]) == 0
    assert out.read_text() == first
    assert [r.hash for r in store.runs()] == [h]


def test_run_file_written_by_hand(store, tmp_path):
    spec = tmp_path / "grid.yaml"
    spec.write_text(yaml.safe_dump({
        "note": "by hand",
        "command": "generate manhattan",
        "args": {"seed": 2},
        "config": {"generators": {"manhattan": {"preset": "manhattan_like", "n_avenues": 3}}},
    }))
    assert main(["run", str(spec)]) == 0
    (run,) = store.runs()
    assert run.note == "by hand"
    graph = json.loads((run.path / "manhattan_manhattan_like_s2.json").read_text())
    assert graph["metadata"]["params"]["n_avenues"] == 3
    spec.write_text(yaml.safe_dump({"command": ["generate", "manhattan"], "args": {"bogus": 1}}))
    with pytest.raises(SystemExit, match="bogus"):
        main(["run", str(spec)])
    spec.write_text(yaml.safe_dump({"command": ["generate", "manhattan"], "args": {"preset": "x"}}))
    with pytest.raises(SystemExit, match="under config"):
        main(["run", str(spec)])


def test_replay_uses_the_recorded_config(store, tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("generators:\n  manhattan:\n    lane_width: 4.25\n")
    _, h = run_argv(["--config", str(cfg), *GEN, "-o", "g.json"])
    assert run_file(store, h)["config"]["generators"]["manhattan"]["lane_width"] == 4.25
    cfg.unlink()  # the config file is gone; run.yaml has its content
    assert main([h]) == 0
    edges = json.loads((store.root / h / "g.json").read_text())["edges"]
    assert {e["lane_width"] for e in edges} == {4.25}


def test_params_file_content_is_in_the_run(store, tmp_path):
    params = tmp_path / "p.yaml"
    params.write_text("preset: manhattan_like\nparams:\n  n_avenues: 3\n")
    _, h = run_argv(["generate", "manhattan", "--params", str(params)])
    params.write_text("n_avenues: 9\n")
    _, h2 = run_argv(["generate", "manhattan", "--params", str(params), "--streets", "2"])
    assert h != h2
    sec = run_file(store, h)["config"]["generators"]["manhattan"]
    assert sec["preset"] == "manhattan_like" and sec["n_avenues"] == 3


def test_replay_errors(store):
    with pytest.raises(SystemExit, match="no run"):
        main(["replay", "abcdef"])
    with pytest.raises(SystemExit, match="too short"):
        main(["replay", "abc"])


def test_replay_show_and_history_grep(store, capsys):
    _, h = run_argv(["--note", "demo", *GEN, "-o", "g.json"])
    capsys.readouterr()
    assert main(["replay", h, "--show"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "python -m cli " + " ".join([*GEN, "-o", "g.json"])
    assert str(store.root / h) in lines[1]
    main(["history", "--grep", "demo"])
    assert h in capsys.readouterr().out


def test_rerun_replaces_the_folder(store):
    _, h = run_argv([*GEN, "-o", "g.json"])
    (store.root / h / "stale.txt").write_text("old")
    assert run_argv([*GEN, "-o", "g.json"])[1] == h
    assert not (store.root / h / "stale.txt").exists()
