"""cli.run_side_by_side: the tasks after the .xodr run in forked processes at once."""

from __future__ import annotations

import os

import pytest

from cli import run_side_by_side
from roadgen.validate.stages import StageReport

# pytest-xdist workers run threads of their own, so Python warns on fork; the CLI forks
# from its single Python thread.
pytestmark = pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")


def test_tasks_run_in_their_own_processes_and_keep_their_order():
    parent = os.getpid()
    out = run_side_by_side({
        "b": lambda: [StageReport("b", [], [str(os.getpid())])],
        "a": lambda: [StageReport("a", ["an error"])],
    })
    assert list(out) == ["b", "a"]
    (b,), crash = out["b"]
    assert crash is None and int(b.warnings[0]) != parent  # ran in a child
    (a,), _ = out["a"]
    assert a.errors == ["an error"]


def test_a_task_that_raises_or_dies_is_reported():
    def boom() -> list:
        raise RuntimeError("boom")

    out = run_side_by_side({"ok": lambda: [], "boom": boom, "dies": lambda: os._exit(3)})
    assert out["ok"] == ([], None)
    assert out["boom"] == ([], "boom: boom")
    reports, crash = out["dies"]
    assert reports == [] and "exit code 3" in crash


def test_one_task_runs_here():
    parent = os.getpid()
    out = run_side_by_side({"only": lambda: [StageReport("only", [], [str(os.getpid())])]})
    assert int(out["only"][0][0].warnings[0]) == parent
