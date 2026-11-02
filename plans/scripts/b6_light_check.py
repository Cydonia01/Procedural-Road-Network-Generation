"""Stage B6: do the lights switch in esmini? One network, cars, a long run, and
screenshots of one light at two times when its state differs.

  python plans/scripts/b6_light_check.py out/stage_b/b6/city.xodr [--vehicles 50] [--duration 120]

Writes OUTDIR/closeups/<name>_light_<time>.png (OUTDIR = the .xodr's
folder), a 120 s scenario <name>_lights120.xosc, and prints the simulate
summary and the light's states at the two times (from the plan). Run in the
dev container.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

from lxml import etree

from roadgen.opendrive.lights import GREEN, RED, SignalPlan, state_changes
from roadgen.scenario.xosc_builder import ScenarioBuilder, ScenarioParams
from roadgen.sim.screenshot import screenshot
from roadgen.validate.continuity import RoadEval


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xodr", type=Path)
    ap.add_argument("--vehicles", type=int, default=50)
    ap.add_argument("--duration", type=float, default=120.0)
    args = ap.parse_args()
    side = json.loads(args.xodr.with_suffix(".roadmap.json").read_text())
    plan = side["signal_plans"][0]
    sp = SignalPlan(plan["junction"], plan["cycle"], plan["offset"], (), tuple(plan["phases"]))
    sid = min(plan["lights"], key=int)
    # two times, both past 2 s, when the light is green and red
    state, times = {}, {}
    changes = state_changes(sp, args.duration)
    for (t0, st), nxt in zip(changes, changes[1:] + [(args.duration, {})]):
        state.update(st)
        mid = (t0 + nxt[0]) / 2
        if mid > 2.0 and state[sid] in (GREEN, RED) and state[sid] not in times:
            times[state[sid]] = round(mid, 1)
    xosc = args.xodr.with_name(f"{args.xodr.stem}_lights{int(args.duration)}.xosc")
    ScenarioBuilder.from_files(args.xodr).write(ScenarioParams(args.vehicles, seed=1, sim_duration=args.duration), xosc)
    run = subprocess.run(["python", "-m", "cli", "--no-history", "simulate", str(xosc)], capture_output=True, text=True,
                         check=False)
    print((run.stdout + run.stderr).strip().splitlines()[-1])
    root = etree.parse(str(args.xodr)).getroot()
    sig = next(s for s in root.iter("signal") if s.get("id") == sid)
    road = sig.getparent().getparent()
    ev = RoadEval(road)
    s, t = float(sig.get("s")), float(sig.get("t"))
    x, y, hdg = ev.reference(s)
    lx, ly = x - t * math.sin(hdg), y + t * math.cos(hdg)
    z = ev.elevation(s)[0]
    facing = hdg + (math.pi if sig.get("orientation") == "-" else 0.0)  # the light faces against this
    camera = (lx - 9 * math.cos(facing), ly - 9 * math.sin(facing), z + 3.5, facing, 0.05)
    out = args.xodr.parent / "closeups"
    for name, at in sorted(times.items(), key=lambda kv: kv[1]):
        png = screenshot(out / f"{args.xodr.stem}_light_{sid}_{at:g}s_{'green' if name == GREEN else 'red'}.png",
                         xosc=xosc, at=at, camera=camera)
        print(f"light {sid} (junction {plan['junction']}): {('green' if name == GREEN else 'red')} at {at} s -> {png}")


if __name__ == "__main__":
    main()
