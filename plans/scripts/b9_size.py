"""Stage B9 (prompt 12): what an .xodr's bytes are made of, by element type.

  python plans/scripts/b9_size.py OLD.xodr NEW.xodr [--json OUT.json]
  e.g. python plans/scripts/b9_size.py out/stage_b/b7z/manhattan_like.xodr \
           out/stage_b/b8r/manhattan_like.xodr

Every byte of the file is assigned to one category: the serialised element
with its trailing whitespace (its tail), the categories nested as
- road: planView, elevationProfile, lateralProfile, laneOffset, link, type;
  per lane: width, roadMark (with its type/line), link, height, speed, other
  lane children, and the lane element itself; laneSection shells; objects by
  kind (arrow, crosswalk, stopLine, yieldLine, patch, other); signals (dynamic
  apart); the rest of the road element;
- junction: connection, priority, controller, the rest;
- controller; header and the rest of the file.
For each file: bytes per category (MB) and its share; for the pair: growth per
category. Also the share of pure indentation (whitespace text and tails) and of
the digits of numbers in attribute values.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from lxml import etree

NUMBER = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")


def size(e: etree._Element) -> int:
    return len(etree.tostring(e, with_tail=True))


def shell(e: etree._Element) -> int:
    """Bytes of an element without its children (start and end tags, text, tail)."""
    return size(e) - sum(size(c) for c in e)


def object_kind(o: etree._Element) -> str:
    name = o.get("name", "")
    if name.startswith("arrow"):
        return "arrow"
    if o.get("type") in ("crosswalk", "patch"):
        return o.get("type")
    return name if name in ("stopLine", "yieldLine") else "other"


def breakdown(path: Path) -> dict:
    root = etree.parse(str(path)).getroot()
    out: Counter = Counter()
    out["file rest"] += path.stat().st_size - size(root) + shell(root)
    for child in root:
        if child.tag == "road":
            out["road shell"] += shell(child)
            for part in child:
                if part.tag == "lanes":
                    out["lanes shell"] += shell(part)
                    for sec in part:
                        if sec.tag != "laneSection":
                            out[f"lanes/{sec.tag}"] += size(sec)
                            continue
                        out["laneSection shell"] += shell(sec)
                        for side in sec:
                            out["laneSection shell"] += shell(side)
                            for lane in side:
                                out["lane element"] += shell(lane)
                                for c in lane:
                                    key = c.tag if c.tag in ("width", "roadMark", "link", "height", "speed") else "other"
                                    out[f"lane/{key}"] += size(c)
                elif part.tag == "objects":
                    out["objects shell"] += shell(part)
                    for o in part:
                        out[f"object/{object_kind(o)}" if o.tag == "object" else f"objects/{o.tag}"] += size(o)
                elif part.tag == "signals":
                    out["signals shell"] += shell(part)
                    for s in part:
                        key = "dynamic" if s.get("dynamic") == "yes" else "static"
                        out[f"signal/{key}" if s.tag == "signal" else f"signals/{s.tag}"] += size(s)
                else:
                    out[f"road/{part.tag}"] += size(part)
        elif child.tag == "junction":
            out["junction shell"] += shell(child)
            for part in child:
                out[f"junction/{part.tag}"] += size(part)
        else:
            out[child.tag] += size(child)
    total = path.stat().st_size
    text = path.read_text()
    indent = sum(len(m.group()) for m in re.finditer(r">\s+<", text)) - text.count("><")
    digits = sum(len(m.group()) for m in NUMBER.finditer(" ".join(v for e in root.iter() for v in e.attrib.values())))
    assert sum(out.values()) == total, (sum(out.values()), total)
    return {"bytes": total, "categories": dict(out), "indent_bytes": indent, "number_bytes": digits}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    a, b = breakdown(args.old), breakdown(args.new)
    keys = sorted(set(a["categories"]) | set(b["categories"]),
                  key=lambda k: -(b["categories"].get(k, 0) - a["categories"].get(k, 0)))
    print(f"{'category':22s} {'old MB':>8s} {'new MB':>8s} {'growth MB':>10s} {'share of growth':>16s}")
    grow = b["bytes"] - a["bytes"]
    for k in keys:
        x, y = a["categories"].get(k, 0), b["categories"].get(k, 0)
        if max(x, y) < 20_000:
            continue
        print(f"{k:22s} {x / 1e6:8.2f} {y / 1e6:8.2f} {(y - x) / 1e6:10.2f} {100 * (y - x) / grow:15.1f} %")
    for name, d in (("old", a), ("new", b)):
        print(f"{name}: {d['bytes'] / 1e6:.2f} MB, indentation {d['indent_bytes'] / 1e6:.2f} MB "
              f"({100 * d['indent_bytes'] / d['bytes']:.1f} %), numbers {d['number_bytes'] / 1e6:.2f} MB "
              f"({100 * d['number_bytes'] / d['bytes']:.1f} %)")
    if args.json:
        args.json.write_text(json.dumps({"old": {"file": str(args.old), **a}, "new": {"file": str(args.new), **b}},
                                        indent=2) + "\n")


if __name__ == "__main__":
    main()
