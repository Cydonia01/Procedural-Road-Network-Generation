# Slim down the benchmark

## Prompts

- 2026-10-27: "The aim of the project is to train autonomous cars. So, traffic simulation is not an aim. However, I do not understand what they main by scalability experiments in project-explanation.md file. I want project to as clean as posssible, i.e., do not containing unnecessary code or knowledge. So, we need to modify the benchmark if necessary."
- 2026-10-27: "slim it down"

## Decisions

- User: slim the benchmark rather than remove it or keep it (options were remove, slim, keep).
- Claude: "scalability experiments" is a line from the original course brief (how esmini cost grows with network and vehicle count), not a goal of training autonomous cars. What stays useful is the cost of one run, since it bounds training throughput.
- Claude: removed `roadgen/bench/`, the `bench` and `bench-plot` commands, `configs/bench_*.yaml`, the `bench` defaults, `docs/benchmarking.md` and the sweep tests. Git history keeps them.
- Claude: kept `run_monitored` (timeout, memory cap, load time, peak memory) and moved its tests to `tests/test_runner.py`. `simulate` now also logs load time, stepping real-time factor and peak memory.
- Claude: rewrote `project-explanation.md` and the intro lines of the README, architecture doc and Copilot instructions around training autonomous driving.
