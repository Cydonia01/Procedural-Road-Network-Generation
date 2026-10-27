from __future__ import annotations

import sys

from roadgen.sim.runner import run_monitored


# --- process monitor ----------------------------------------------------------------------


def test_monitor_timeout(tmp_path):
    run = run_monitored(["sleep", "5"], tmp_path / "o.txt", timeout=0.3)
    assert run.killed == "timeout"
    assert run.wall < 2.0


def test_monitor_memory_cap_and_peak(tmp_path):
    hog = [sys.executable, "-c", "import time; b = bytearray(300 * 2**20); time.sleep(5)"]
    run = run_monitored(hog, tmp_path / "o.txt", timeout=20, memory_cap_mb=100)
    assert run.killed == "memory"
    assert run.peak_rss_mb > 100


def test_monitor_first_line_timing(tmp_path):
    import re

    script = "import time; time.sleep(0.4); print('[0.000] go', flush=False); time.sleep(0.3)"
    run = run_monitored([sys.executable, "-c", script], tmp_path / "o.txt",
                        first_line_pattern=re.compile(rb"\[\d+\.\d+\]"))
    # Unflushed print still shows up immediately: the pty makes stdout line-buffered.
    assert 0.35 < run.first_match < 0.65
    assert 0.65 < run.wall < 1.2
    assert b"[0.000] go" in (tmp_path / "o.txt").read_bytes()


def test_monitor_peak_of_short_process(tmp_path):
    quick = [sys.executable, "-c", "b = bytearray(80 * 2**20)"]
    run = run_monitored(quick, tmp_path / "o.txt")
    assert run.killed is None and run.returncode == 0
    assert run.peak_rss_mb > 80  # from wait4 even if the poll never saw it
