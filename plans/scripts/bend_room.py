"""How much radius do degree-2 bends have room for? Arc-only, ignores spirals."""
import json, math, sys
from collections import defaultdict
G, EF = 9.81, 0.22  # e + f
JUNCTION_TRIM = 10.0  # rough trim at real junctions [m]
for path in sys.argv[1:]:
    d = json.load(open(path))
    nodes = {n["id"]: n for n in d["nodes"]}
    inc = defaultdict(list)
    for e in d["edges"]:
        inc[e["from_node"]].append(e); inc[e["to_node"]].append(e)
    L = lambda e: math.dist((nodes[e["from_node"]]["x"], nodes[e["from_node"]]["y"]), (nodes[e["to_node"]]["x"], nodes[e["to_node"]]["y"]))
    bends = [n for n, es in inc.items() if len(es) == 2]
    lane_change = straightish = fit = 0
    ratios = []
    for n in bends:
        a, b = inc[n]
        if (a["lanes_forward"] + a["lanes_backward"]) != (b["lanes_forward"] + b["lanes_backward"]):
            lane_change += 1
        hd = []
        for e in (a, b):
            o = nodes[e["to_node"] if e["from_node"] == n else e["from_node"]]
            hd.append(math.atan2(o["y"] - nodes[n]["y"], o["x"] - nodes[n]["x"]))
        defl = math.pi - abs((hd[0] - hd[1] + math.pi) % (2 * math.pi) - math.pi)
        if defl < math.radians(2):
            straightish += 1; continue
        room = []
        for e in (a, b):
            other = e["to_node"] if e["from_node"] == n else e["from_node"]
            room.append(L(e) / 2 if len(inc[other]) == 2 else max(L(e) - JUNCTION_TRIM, 0))
        r_fit = min(room) / math.tan(defl / 2)
        v = min(a["speed_limit"], b["speed_limit"])
        r_need = v * v / (G * EF)
        fit += r_fit >= r_need
        ratios.append(math.sqrt(G * EF * r_fit) / v)
    ratios.sort()
    k = len(ratios)
    print(f"{path}: {len(bends)} bends / {sum(len(es)>=2 for es in inc.values())} junction nodes, "
          f"{lane_change} lane-count changes, {straightish} nearly straight (<2 deg); "
          f"of {k} real bends {fit} ({100*fit/max(k,1):.0f}%) fit R_min at the speed limit; "
          f"advisory/limit median {ratios[k//2]:.2f}, p10 {ratios[k//10]:.2f}")
